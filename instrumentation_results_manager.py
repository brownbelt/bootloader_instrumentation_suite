#! /usr/bin/env python
# MIT License

# Copyright (c) 2017 Rebecca ".bx" Shapiro

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from config import Main
import config
from doit import loader
from doit.task import dict_to_task
from doit.task import DelayedLoader
from doit.task import Task
from doit.tools import run_once
from doit.dependency import UptodateCalculator
from doit.cmd_base import TaskLoader
from doit.action import PythonAction
import sys
import traceback
import time
import IPython
import os
import re
import atexit
import glob
import importlib
import difflib
from doit.action import CmdAction
from doit import create_after
from doit.tools import LongRunning, Interactive, PythonInteractiveAction
import inspect
import string
import pure_utils
import external_source_manager
from doit.tools import create_folder
import tempfile
import parse_am37x_register_tables
import addr_space
import staticanalysis
import labeltool
import traceback
import substage
import yaml
import qemu_raw_trace
import db_info
from doit import exceptions
import qemu_raw_trace


class DelTargetAction(PythonInteractiveAction):

    def execute(self, out, err):
        ret = super(DelTargetAction, self).execute(sys.stdout, err)
        if isinstance(ret, exceptions.CatchedException) or isinstance(ret, Exception):
            if self.task:
                for f in self.task.targets:
                    cmd = "rm -rf %s" % f
                    os.system(cmd)
        return ret


_manager_singleton = None
_diabled_tasks = []


def task_manager(test_id=None):
    class TestTaskManager(object):
        def __init__(self, test_id):
            self.ALL_GROUPS = 0
            self.test_id = test_id
            self.tasks = {}
            self.enabled = [self.ALL_GROUPS]
            self.taskmanagers = {}
            self.grouporder = []

        def enable(self, subgroup):
            self.enabled.append(subgroup)

        def add_mgr(self, mgr):
            self.taskmanagers[mgr.subgroup] = mgr

        def add_tasks(self, task_list, subgroup):
            l = self.tasks.get(subgroup, [])
            l.extend(task_list)
            self.tasks[subgroup] = l

        def list_tasks(self):
            class List():
                def __init__(self, obj, subgroup):
                    self.obj = obj
                    self.subgroup = subgroup

                def list_tasks(self):
                    return self.obj._list_tasks(self.subgroup)
            ts = []
            for k in self.grouporder:
                ts.append(("task_%s_subgroup" % self.build_name(k),
                           List(self, k).list_tasks))
            ts.append(("task_%s_subgroup_all_" % self.build_name(),
                       List(self, self.ALL_GROUPS).list_tasks))
            return ts

        def _list_tasks(self, subgroup):
            if subgroup == self.ALL_GROUPS:
                vallists = [self.tasks[t] for t in self.grouporder]
                alldeps = []
                allfiles = []
                for subgroup in self.grouporder:
                    tasks = self.tasks[subgroup]
                    subfiles = []
                    if subgroup in self.enabled:
                        for t in tasks:
                            #print "%s enable %s--------" % (subgroup, t.name)
                            subfiles.extend(t.file_dep)
                            alldeps.append(self.build_name(subgroup))
                            allfiles.extend(t.file_dep)
                    yield {
                        'basename': subgroup,
                        'name': None,
                        'file_dep': subfiles,
                    }
                yield {
                    'basename': "ALL_GROUPS",
                    'name': None,
                    'task_dep': self.grouporder,
                }

            else:
                for inst in self.tasks[subgroup]:
                    r = {
                        'actions': inst.actions,
                        'targets': inst.targets,
                        'file_dep': inst.file_dep,
                        'task_dep': inst.task_dep
                    }
                    r['basename'] = inst.name
                    r['name'] = self.build_name(subgroup)
                    r.update(inst.other)
                    if subgroup not in self.enabled:
                        del r['targets']
                        del r['actions']
                        del r['file_dep']
                        del r['task_dep']
                    else:
                        #print "enable :--:%s %s -> %s" % (self.task_name(inst,
                        #                                                  subgroup),
                        #                                   r['file_dep'], r['targets'])
                        yield r

        def task_name(self, task, subgroup):
            return "%s:%s" % (task.name, self.build_name(subgroup))

        def build_name(self, subgroup=""):
            if not subgroup:
                return self.test_id
            else:
                return "%s:%s" % (subgroup, self.test_id)

    global _manager_singleton
    if not _manager_singleton:
        if test_id is None:
            raise Exception("test_id must be defined")
        _manager_singleton = TestTaskManager(test_id)
    return _manager_singleton


class TestTask(object):
    def __init__(self, name, unique=False):
        if unique:
            self.name = name
        else:
            self.name = "%s_%s" % (name, self.__class__.__name__)
        for i in ["actions", "targets", "file_dep", "other", "task_dep"]:
            if not hasattr(self, i):
                default = {} if i == 'other' else []
                listname = "list_%s" % i
                if i is not "other":
                    val = getattr(self, listname)() if hasattr(self, listname) else default
                else:
                    val = getattr(self, listname)() if hasattr(self, listname) else default
                setattr(self, i, val)
        self.verbosity = 2


class CopyFileTask(TestTask):
    def __init__(self, src, dst):
        super(CopyFileTask, self).__init__(dst, True)
        self.src = src
        self.dst = dst
        dirdest = os.path.dirname(self.dst)
        self.task_dep = [dirdest] if MkdirTask.exists(dirdest) else []
        self.actions = ["cp -f %s %s" % (self.src, self.dst)]
        self.targets = [self.dst]


class MkdirTask(TestTask):
    dirs = set()

    @classmethod
    def exists(cls, d):
        return d in cls.dirs

    def __init__(self, d):
        super(MkdirTask, self).__init__(d, True)
        self.dst = d
        self.actions = [(create_folder, [self.dst])]
        # if self.dst in MkdirTask.dirs:
        #    traceback.print_stack()
        MkdirTask.dirs.add(self.dst)
        self.targets = [self.dst]
        # if self.exists(os.path.dirname(self.dst)):
        #    self.task_dep = [os.path.dirname(self.dst)]


class CmdTask(TestTask):
    def __init__(self, cmds, file_deps, tgts, name):
        super(CmdTask, self).__init__(name)
        self.fdeps = file_deps
        self.targets = tgts
        self.actions = cmds
        self.file_dep = self.fdeps


class DelTargetActionTask(TestTask):
    def __init__(self, fn, file_dep, tgts, name):
        super(DelTargetActionTask, self).__init__(name)
        self.targets = tgts
        self.file_dep = file_dep

        self.del_fn = deltargets
        self.actions = [DelTargetAction(fn)]


class ActionListTask(TestTask):
    def __init__(self, actions, file_deps, tgts, name):
        super(ActionListTask, self).__init__(name)
        self.fdeps = file_deps
        self.targets = tgts
        self.actions = actions
        self.file_dep = self.fdeps

    def __repr__(self):
        return "Actions %s" % self.actions


class ResultsLoader(object):
    def __init__(self, test_id, subgroup, run_task=True):
        self.test_id = test_id
        self.subgroup = subgroup
        self.task_adders = []
        self.task_manager = task_manager(test_id)
        self.task_manager.grouporder.append(self.subgroup)
        self.run_task = run_task
        if self.run_task:
            self.enable()

    def get_build_name(self):
        return self.task_manager.build_name(self.subgroup)

    def enable(self):
        self.task_manager.enable(self.subgroup)

    def _add_tasks(self):
        for i in self.task_adders:
            t = i()
            self.task_manager.add_tasks(t, self.subgroup)
        self.task_manager.add_mgr(self)

    def _copy_file(self, path, dst):
        return CopyFileTask(path, dst)

    def _mkdir(self, path):
        return MkdirTask(path)

    def save_config(self, k, v):
        Main.set_config(k,  v)
        def save():
            return {k: v}
        return ActionListTask([(save,)], [], [], "save_%s" % k)


class PostTraceLoader(ResultsLoader):
    _processes_types = {'consolidate_writes': {'fn':
                                               "_histogram"},
                        'policy_check': {'fn':
                                         "_policy_check"},
                        'noop': {'fn': "_noop"},
                        'browse_db': {'fn':
                                      "_browse_db"},
                        'process_watchpoints': {"fn":
                                                "_watchpoints",
                                                "traces": ["watchpoint"]}}
    supported_types = _processes_types.iterkeys()

    def __init__(self, processes, run):
        test_id = Main.get_config("test_instance_id")
        super(PostTraceLoader, self).__init__(test_id, "post_trace", run)
        self.trace_id = Main.get_config("trace_id")
        self.data_dir = Main.get_config("trace_data_dir")
        self.name = Main.get_config("trace_name")
        self.stages = Main.get_config("trace_stages")
        self.hw = Main.get_config("trace_hw")
        self.tracenames = Main.get_config("trace_traces")
        self.processes = list(set(processes))
        self.task_adders = [self._setup_tasks,
                            self._process_tasks]
        self._add_tasks()

    def _test_path(self, rel=""):
        return os.path.join(self.data_dir, rel)

    def _process_path(self, p, rel=""):
        return os.path.join(self._test_path(p), rel)

    def _setup_tasks(self):
        tasks = []
        targets = []
        for (k, v) in self._processes_types.iteritems():
            if "traces" in v.iterkeys() and not all(map(lambda t: t in v["traces"],
                                                        self.tracenames)):
                continue

            tasks.append(self._mkdir(self._process_path(k)))
        return tasks

    def _process_tasks(self):
        tasks = []
        uptodate = {"uptodate": [True]}
        not_uptodate = {"uptodate": [False]}
        for (k, v) in self._processes_types.iteritems():
            if "traces" in v.iterkeys() and not all(map(lambda t: t in v["traces"],
                                                        self.tracenames)):
                continue
            if k not in self._processes_types.iterkeys():
                enabled = False
            else:
                enabled = True
            proc = getattr(self, v["fn"])
            ts = proc(k, enabled)
            if k not in self.processes:
                for t in ts:
                    t.other.update(uptodate)
            else:
                for t in ts:
                    t.other.update(not_uptodate)
                tasks.extend(ts)
        return tasks

    def _watchpoints(self, name, enabled):
        tasks = []
        raw_output = Main.get_config("trace_events_output")
        target = []
        test_db = {}
        test_db_done = {}

        class Do():
            def __init__(self, stage, events, raw):
                self.stage = stage
                self.raw = raw
                self.events = events

            def __call__(self):
                qemu_raw_trace.process_and_import(self.events,
                                                  self.raw,
                                                  self.stage)
        events = Main.get_config("all_qemu_evnts")
        for s in self.stages:
            n = s.stagename
            test_db[n] = self._test_path("tracedb-%s.h5" % n)
            test_db_done[n] = self._test_path("tracedb-%s.completed" % n)
            tasks.append(ActionListTask([PythonInteractiveAction(Do(s, events, raw_output)),
                                         "touch %s" % test_db_done[n]],
                                        [raw_output, events],
                                        [test_db_done[n], test_db[n]],
                                        "import_watchpoints_to_tracedb"))
        Main.set_config("trace_db", lambda s: test_db[s.stagename])
        Main.set_config("trace_db_done", lambda s: test_db_done[s.stagename])
        return tasks

    def _browse_db(self, name, enabled):
        tasks = []

        class Do():
            def __init__(self):
                pass

            def __call__(self):
                rwe = self
                IPython.embed()
        a = ActionListTask([PythonInteractiveAction(Do())],
                           [], [], name)
        tasks.append(a)
        return tasks

    def _histogram(self, name, enabled):

        tasks = []
        deps = []
        class Do():
            def __init__(self, s, o, o2):
                self.s = s
                self.o = o
                self.o2 = o2

            def __call__(self):
                db_info.create(self.s, "policydb")
                db_info.get(self.s).consolidate_trace_write_table()
                db_info.get(self.s).generate_write_range_file(self.o, self.o2)
        outs = {}
        el_file = {}
        fns = {}
        outfile = {}
        for s in self.stages:
            n = s.stagename
            deps.append(Main.get_config('trace_db_done', s))
            deps.append(Main.get_config("policy_db", s))
            deps.append(Main.get_config("policy_db_done", s))
            el_file[n] = self._process_path(name, "substages-%s.el" % n)
            fns[n] = self._process_path(name, "%s_fn_lists" % n)
            outfile = self._process_path(name,
                                         "%s-write_range_info.txt" % n)
            outfile2 = self._process_path(name,
                                         "%s-write_range_info.csv" % n)
            a = ActionListTask([PythonInteractiveAction(Do(s, outfile, outfile2))],
                               deps, [outfile, fns[n], el_file[n]], name)
            outs[s.stagename] = outfile
            # if "watchpoint" in self.tracenames:
            #    a.task_dep = ["import_watchpoints_to_tracedb"]
            tasks.append(a)
        Main.set_config("policy_trace_el", lambda s: el_file[s.stagename])
        Main.set_config("policy_trace_fnlist_dir", lambda s: fns[s.stagename])
        Main.set_config("consolidate_writes_done", lambda s: outs[s.stagename])
        return tasks

    def _noop(self, name, enabled):
        return []

    def _policy_check(self, name, enabled):
        tasks = []
        tp_db = {}
        deps = []
        tp_db_done = {}

        class Do():
            def __init__(self, s):
                self.s = s

            def __call__(self):
                db_info.create(self.s, "policydb")
                db_info.get(self.s).check_trace()

        for s in Main.get_config("stages_with_policies"):
            n = s.stagename
            tp_db[n] = self._process_path(name, "policy-tracedb-%s.h5" % n)
            tp_db_done[n] = self._process_path(name, "policy-tracedb-%s.completed" % n)
            targets = []
            deps.append(Main.get_config("policy_db", s))
            deps.append(Main.get_config("policy_db_done", s))
            if s in self.stages:
                targets = [tp_db_done[n], tp_db[n]]
                a = ActionListTask([PythonInteractiveAction(Do(s)),
                                    "touch %s" % tp_db_done[n]],
                                   deps, targets,
                                   "%s_postprocess_trace_policy" % (n))
                # a.task_dep.append(os.path.dirname(tp_db_done[n]))
                # if os.path.exists(Main.get_config("calltrace_db", s)):
                #    el = Main.get_config("policy_trace_el", s)
                #    a.file_dep.append(os.path.basename(el))
                    # a.task_dep.append("consolidate_writes_ActionListTask:post_trace")
                    # a.file_dep.append(el)
                # a.task_dep.append(os.path.dirname(Main.get_config("policy_db", s)))
                # a.task_dep.append("policy")
                a.uptodate = [(False)]
                tasks.append(a)

        Main.set_config("policy_trace_db", lambda s: tp_db[s.stagename])
        Main.set_config("policy_trace_done", lambda s: tp_db_done[s.stagename])
        return tasks


class TraceTaskLoader(ResultsLoader):
    def __init__(self, stages, hw, tracenames,
                 trace_name,
                 create, quick, run_tasks,
                 print_cmds, new_trace):
        self.print_cmds = print_cmds
        test_id = Main.get_config("test_instance_id")
        super(TraceTaskLoader, self).__init__(test_id,
                                              "trace",
                                              (run_tasks or new_trace) and not print_cmds)
        self.test_root = Main.get_config("test_instance_root")
        self.create = create
        self.toprint = []
        self.quick = quick
        self.quit = True
        self.trace_id = trace_name
        self.stages = stages
        self.tracenames = tracenames
        self.stagenames = [s.stagename for s in self.stages]
        self.hw = hw
        self.hwname = self.hw.name
        self.task_adders = [self._collect_data]
        self._add_tasks()

    def _dest_dir_root_path(self, rel=""):
        return os.path.join(self.test_root, "trace_data", rel)

    def _test_path(self, rel=""):
        return os.path.join(self._dest_dir_root_path(self.trace_id), rel)

    def _writes(self):
        tasks = []
        Main.set_config('trace_stages', self.stages)
        tasks.extend(self._collect_writes_data())
        return tasks

    def _collect_data(self):
        gdb_commands = []
        done_commands = []
        done_targets = []
        gdb_file_dep = []
        gdb_targets = []
        file_dep = []
        gdb_tasks = []
        tasks = []
        gdb_setup = []
        handler_path = os.path.join(Main.hw_info_path, Main.hardwareclass, Main.task_handlers)
        sys.path.append(os.path.dirname(handler_path))
        name = re.sub(".py", "", os.path.basename(handler_path))
        mod = importlib.import_module(name)
        if "watchpoint" in self.tracenames and len(self.tracenames) > 1:
            raise Exception("watchpoints cannot be combined with breakpoint-type traces")
        config = {}
        if hasattr(mod, self.hwname):
            handler = getattr(mod, self.hwname)
            stages_with_policies = Main.get_config("stages_with_policies")
            enabled_stages = Main.get_config("enabled_stages")
            policies = {}
            for s in [Main.stage_from_name(st) for st in enabled_stages]:
                if s.stagename in [a.stagename for a in stages_with_policies]:
                    policies[s.stagename] = Main.get_config("policy_name", s)
            test_id = Main.get_config("test_instance_id")
            handler_tasks = handler(Main, Main.get_bootloader_cfg(),
                                    self.stages,
                                    policies,
                                    self.hw,
                                    Main.object_config_lookup("Software",
                                                              self.hw.host_software),
                                    test_id,
                                    self.trace_id,
                                    self._test_path(),
                                    "watchpoint" in self.tracenames,
                                    self.quick)

            (newtask, c, g_setup, g, gdep,
             gtargets, d, dtargets) = self._process_handler(handler_tasks,
                                                            "hardware_handler", True)
            config.update(c)
            gdb_setup.extend(g_setup)
            # ignore any gdb/done commands/deps/targets
            hwtaskname = newtask.name
        for tracename in self.hw.tracing_methods:
            if not hasattr(mod, tracename):
                continue
            trace_task_handler = getattr(mod, tracename)
            trace_output = trace_task_handler(Main,
                                              config,
                                              self.stages,
                                              policies,
                                              self.hw,
                                              test_id,
                                              self.trace_id,
                                              self._test_path(),
                                              self.quick)
            (ttask, c, g_setup, g, gdep,
             gtargets, d,
             dtargets) = self._process_handler(trace_output,
                                               "tracing_hardware_handler-%s" % tracename,
                                               tracename in self.tracenames)
            config.update(c)
            gdb_setup.extend(g_setup)
            if tracename in self.tracenames:
                if g:
                    if gdb_commands:
                        gdb_commands.extend(g[1:])
                    else:
                        gdb_commands = g
                    gdb_commands.extend(g_setup)
                    gdb_tasks.append(newtask)
                    done_commands.extend(d)
                    gdb_file_dep.extend(gdep)
                    gdb_targets.extend(gtargets)
                    done_targets.extend(dtargets)
                else:
                    newtask.actions.extend(d)
                    newtask.targets.extend(dtargets)
        if gdb_commands:
            gdb = " ".join(gdb_commands)
            if any(['gdb_tools' in g for g in gdb_commands]):
                gdb += " -ex 'gdb_tools go -p'"
            gdb += " -ex 'c'"
            if self.quit:
                gdb += " -ex 'monitor quit' -ex 'monitor exit' -ex 'q'"
            gdb += " &&  true"
            c = CmdTask([Interactive(gdb)] + done_commands,
                        gdb_file_dep,
                        gdb_targets + done_targets, "gdb_tracing")
            newtask = self.merge_tasks(newtask, c)
            for s in self.stages:
                newtask.file_dep.extend([Main.get_config("policy_file", s),
                                         Main.get_config("regions_file", s)])
                newtask.file_dep.extend([Main.get_config("test_config_file")])
            newtask.file_dep.append(Main.get_config("test_config_file"))
            tasks.append(newtask)
        else:
            newtask.actions.extend(done_commands)
            newtask.targets.extend(done_targets)
            newtask.file_dep.append(Main.get_config("test_config_file"))
            for s in self.stages:
                ttask.file_dep.extend([Main.get_config("policy_file", s),
                                       Main.get_config("regions_file", s)])
            ttask.file_dep.extend([Main.get_config("test_config_file")])
            newtask.file_dep.extend([Main.get_config("test_config_file")])
            ttask.name = "trace-" + ttask.name
            tasks.extend([newtask, ttask])
        sys.path.pop()
        if self.print_cmds:
            self.toprint = newtask
            return []
        else:
            return tasks

    def do_print_cmds(self):
        if not self.toprint:
            return
        print "----------------------------------------"
        for a in self.toprint.actions:
            print a
        print "----------------------------------------"

    def merge_tasks(self, t1, t2):
        for i in ["file_dep", "actions", "targets"]:
            getattr(t1, i).extend(getattr(t2, i))
        t1.other.update(t2.other)
        return t1

    def _process_handler(self, handler_tasks, name, save):
        deps = []
        targets = []
        actions = []
        configs = {}
        gdb_commands = []
        gdb_setup = []
        done_commands = []
        gdb_targets = []
        gdb_file_dep = []
        done_targets = []
        for (n, c) in handler_tasks:
            a = None
            if n == "long_running":
                a = LongRunning(c)
            elif n == "interactive":
                a = Interactive(c)
            elif n == "set_config":
                for (k, v) in c.iteritems():
                    Main.set_config(k, v)
            elif n == "command":
                a = c
            elif n == "configs":
                configs.update(c)
            elif n == "targets":
                targets.extend(c)
            elif n == "gdb_commands":
                gdb_commands.extend(c)
            elif n == "gdb_setup":
                gdb_setup.extend(c)
            elif n == "file_dep":
                deps.extend(c)
            elif n == "done_commands":
                done_commands.extend(c)
            elif n == "gdb_targets":
                gdb_targets.extend(c)
            elif n == "done_targets":
                done_targets.extend(c)
            elif n == "gdb_file_dep":
                gdb_file_dep.extend(c)
            else:
                raise Exception("unknown hardware trace handler tag '%s' (%s)" % (n, c))
            if a:
                actions.append(a)
        task = ActionListTask(actions, deps,
                              targets, name)
        return (task, configs, gdb_setup, gdb_commands,
                gdb_file_dep,
                gdb_targets, done_commands, done_targets)


class TraceTaskPrepLoader(ResultsLoader):
    def __init__(self, instrumentation_task, trace_name, create, run_tasks,
                 print_cmds, create_test_only, hook=False):
        self.print_cmds = print_cmds
        test_id = Main.get_config("test_instance_id")
        super(TraceTaskPrepLoader, self).__init__(test_id, "trace_prep", not create_test_only)
        self.test_root = Main.get_config("test_instance_root")
        self.create = create
        self.trace_id = self.get_trace_name(test_id, trace_name, create, hook)
        self.new = True
        self.config_path = self._test_path("config.yml")
        if instrumentation_task:
            self.stagenames = instrumentation_task['stages']
            self.hwname = instrumentation_task['hw']
            self.tracenames = instrumentation_task['traces']
        else:
            self.new = False
            with open(self.config_path, 'r') as f:
                settings = yaml.load(f)
                self.stagenames = settings['stages']
                self.hwname = settings['hw']
                self.tracenames = settings['traces']
        self.hw = Main.get_hardwareclass_config().hardware_type_cfgs[self.hwname]
        self.stages = [Main.stage_from_name(s) for s in self.stagenames]
        Main.set_config('enabled_stages', self.stagenames) # update enabled stages
        self.name = "%s.%s.%s" % (self.hwname,
                                  "-".join(self.tracenames), "-".join(self.stagenames))
        self.namefile = self._test_path(self.name)

        Main.set_config("trace_name", self.name)
        Main.set_config("trace_stages", self.stages)
        Main.set_config("trace_hw", self.hw)
        Main.set_config("trace_traces", self.tracenames)
        Main.set_config("trace_id", self.trace_id)
        self.task_adders = [self._setup_tasks, self._openocd_tasks]
        self._add_tasks()

    @classmethod
    def _existing_trace_ids(cls, instance_id):
        for f in glob.glob("%s/*" % os.path.join(cls.instance_root(instance_id),  "trace_data")):
            if os.path.isdir(f) and not os.path.basename(f) == "trace_data-by_name":
                yield os.path.basename(f)

    @classmethod
    def get_trace_name(cls, instance_id, trace_id, create=False, hook=False):
        if create:
            trace_id = cls.create_new_id(instance_id)
        elif trace_id is None:  # get last id
            trace_id = sorted(cls._existing_trace_ids(instance_id))[-1]
        else:
            if not hook:
                existing = sorted(cls._existing_trace_ids(instance_id))
                if trace_id not in existing:
                    try:
                        i = int(trace_id)
                    except:
                        i = None
                    if i is not None:
                        i = cls._format_id(i)
                        if i in existing:
                            trace_id = i
                    res = difflib.get_close_matches(trace_id, existing, 1, 0)
                    if not res:
                        if len(existing) == 0:
                            raise Exception("No exising trace result dirs")
                        trace_id = existing[-1]
                    else:
                        trace_id = res[0]
        return cls._format_id(trace_id)

    @classmethod
    def test_path(cls, instance_id, trace_id):
        return os.path.join(cls.instance_root(instance_id), "trace_data", trace_id)

    @classmethod
    def _format_id(cls, num):
        return str(num).zfill(8)

    def _openocd_tasks(self):
        tasks = []
        tracefile = {}

        if not self.hw.host_software == "openocd":
            return tasks

        d = os.path.join(Main.get_config("test_instance_root"), "openocd")
        tasks.append(self._mkdir(d))
        Main.set_config("openocd_data_dir", d)
        ocd_cached = os.path.join(d, "ocdinit")
        sw = os.path.join(Main.object_config_lookup("Software",
                                                    "openocd"))
        search = os.path.join(sw.root, "tcl")
        jtag_config = self.hw.default_jtag
        jtagfile = Main.object_config_lookup("JtagConfig", "flyswatter2")
        hw_config = os.path.join(self.hw.openocd_cfg)
        hdir = os.path.join(Main.hw_info_path, Main.get_hardwareclass_config().name)
        ocdinit = os.path.join(hdir, "ocdinit")
        Main.set_config("openocd_init_file", ocd_cached)
        tasks.append(self._copy_file(ocdinit, ocd_cached))
        Main.set_config("openocd_init_file", ocd_cached)
        Main.set_config('openocd_jtag_config_path', jtagfile.cfg_path)
        Main.set_config('openocd_hw_config_path', hw_config)
        Main.set_config('openocd_search_path', search)
        return tasks

    @classmethod
    def create_new_id(cls, instance_id):
        num = 0
        existing = sorted(cls._existing_trace_ids(instance_id))
        if len(existing) == 0:
            return cls._format_id(0)
        while True:
            if cls._format_id(num) in existing:
                num += 1
            else:
                break
        return cls._format_id(num)

    # def existing_trace_ids(self, instance_id):
    #     for f in glob.glob("%s/*" % self._dest_dir_root_path(instance_id)):
    #         if os.path.isdir(f) and not os.path.basename(f) == "trace_data-by_name":
    #             yield os.path.basename(f)

    @classmethod
    def instance_root(cls, instance_id):
        return os.path.join(Main.test_data_path, instance_id)

    def _dest_dir_root_path(self, rel=""):
        return os.path.join(self.test_root, "trace_data", rel)

    def _test_path(self, rel=""):
        return os.path.join(self._dest_dir_root_path(self.trace_id), rel)

    def _setup_tasks(self):
        tasks = []
        deps = []
        Main.set_config("trace_data_root", self._test_path())
        tasks.append(self._mkdir(self._test_path()))
        symlink_dir = os.path.join(self._dest_dir_root_path(), "trace_data-by_name")
        tasks.append(self._mkdir(symlink_dir))
        target_dir = os.path.join(symlink_dir, os.path.basename(self.namefile))
        tasks.append(self._mkdir(target_dir))
        target_file = os.path.join(target_dir, self.trace_id)
        tasks.append(CmdTask(["ln -s -f %s %s" % (self._test_path(), target_file)],
                             [], [target_file], "symlink-%s" % target_file))
        Main.set_config("trace_data_dir", self._test_path())

        def write(f, stagenames, hwname, tracenames):
            with open(f, "w") as fconfig:
                contents = """
stages: [{}]
hw: {}
traces: [{}]
"""
                filecontents = contents.format(", ".join(stagenames), hwname,
                                               ", ".join(tracenames))
                fconfig.write(filecontents)
        Main.set_config("test_config_file", self.config_path)
        a = ActionListTask([(write, [self.config_path,
                                     self.stagenames, self.hwname, self.tracenames])],
                           [], [self.config_path], "test_config_file")
        try:
            os.makedirs(self._test_path())
        except Exception as e:
            pass
        if not self.new:
            a.other = {'uptodate': [False]}
        tasks.append(a)
        c = CmdTask(["touch %s" % self.namefile], [],
                    [self.namefile], "test_name_file")
        if not self.new:
            c.other = {'uptodate': [False]}
        tasks.append(c)
        return tasks


class InstrumentationTaskLoader(ResultsLoader):
    def __init__(self, boot_task, test_id,
                 enabled_stages, create, gitinfo, manager, build, rm_tmp=True):
        super(InstrumentationTaskLoader, self).__init__(test_id, "instance", True)
        self.create = create
        self.build = build
        self.gitinfo = gitinfo
        self.manager = manager
        self.enabled_stages = enabled_stages
        self.bootloader = Main.get_bootloader_cfg()
        self.test_data_path = Main.test_data_path
        self.bootloader_path = boot_task.root_dir
        self.boot_stages = Main.config_class_lookup("Bootstages")
        self.hardwareclass = Main.get_hardwareclass_config()
        self.test_id = test_id
        self.boot_task = boot_task
        self.rm_tmp = rm_tmp
        hwname = Main.get_hardwareclass_config().name
        bootname = Main.get_bootloader_cfg().software
        hdir = os.path.join(Main.hw_info_path, hwname)
        bootdir = os.path.join(hdir, bootname)
        Main.set_config("test_instance_id", test_id)
        self.default_file_paths = {
                                   "reglist": os.path.join(hdir, "regs.csv"),
                                   "bootloaderdata": bootdir}
        Main.set_config("hardware_data_dir",  hdir)
        Main.set_config("bootloader_data_dir",  bootdir)
        Main.set_config("stages_with_policies", {})  # none yet
        self.task_adders = [self._image_tasks, self._reg_tasks, self._qemu_tasks,
                            self._staticanalysis_tasks, self._addr_map_tasks]
        self._add_tasks()

    def _full_path(self, rel=""):
        return os.path.join(self.test_data_path, self.test_id, rel)

    def _boot_src_path(self, rel=""):
        return os.path.join(self.bootloader_path, rel)

    def _suite_src_path(self, rel=""):
        return os.path.join(Main.config.test_suite_path, rel)

    def _qemu_tasks(self):
        tasks = []
        bootinfo = self.default_file_paths["bootloaderdata"]
        tracefile = {}
        d = self._full_path("qemu-events")
        tasks.append(self._mkdir(d))
        for s in self.boot_stages:
            tracefile_src = os.path.join(bootinfo, s.stagename,
                                         "%s-events" % s.stagename)
            tracefile[s.stagename] = os.path.join(d,
                                                  "%s-events" % s.stagename)
            tasks.append(self._copy_file(tracefile_src, tracefile[s.stagename]))
        Main.set_config("trace_events_file", lambda s: tracefile[s.stagename])
        all_events = os.path.join(Main.object_config_lookup("Software",
                                                            "qemu").root, "trace-events")
        cached_events = self._full_path("qemu-trace-events")
        tasks.append(self._copy_file(all_events, cached_events))
        Main.set_config("all_qemu_evnts", cached_events)
        return tasks

    def _reg_tasks(self):
        tasks = []
        regkey = "reglist"
        reglist = self._full_path("regs.csv")
        default_reglist = self.default_file_paths[regkey]
        pdf = Main.get_hardwareclass_config().tech_reference
        deps = [pdf]

        def regaction():
            if os.path.exists(default_reglist):
                deps.append(default_reglist)
            else:
                # recreate default reglist
                parse_am37x_register_tables.parse(pdf, default_reglist)

            return os.system("cp %s %s" % (default_reglist, reglist)) == 0
        Main.set_config(regkey, reglist)

        rtask = ActionListTask([(regaction, )], deps, [reglist], "create_reg_csv")
        rtask.task_dep.append(self._full_path())
        tasks.append(rtask)
        return tasks

    def _addr_map_tasks(self):
        tasks = []
        dstdir = self._full_path("mmap")
        actions = []
        Main.set_config("mmap_dir", dstdir)
        tasks.append(self._mkdir(dstdir))
        mmapdb_path = os.path.join(dstdir, "mmap.h5")
        mmapdb_done_path = os.path.join(dstdir, "mmap.completed")
        Main.set_config("mmapdb", mmapdb_path)
        Main.set_config("mmapdb_done", mmapdb_done_path)

        class addr_space_setup():
            def __call__(self):
                done_target = Main.get_config("mmapdb_done")
                target = Main.get_config("mmapdb")
                if os.path.exists(target):
                    os.remove(target)
                db_info.create("any", "mmapdb")
                return os.system("touch %s" % done_target) == 0
        a = DelTargetAction(addr_space_setup())

        actions.append(a)
        deps = [Main.get_config("stage_elf", s) for s in
                list(Main.get_bootloader_cfg().supported_stages.itervalues())]
        deps.append(Main.get_config("reglist"))
        rtask = ActionListTask(actions, deps,
                               [mmapdb_path, mmapdb_done_path], "generate_addr_info")
        rtask.task_dep.append(dstdir)
        tasks.append(rtask)

        return tasks

    def _staticanalysis_tasks(self):
        tasks = []
        # db name for each stage
        staticdb = {}
        staticdb_done = {}
        for s in [Main.stage_from_name(st) for st in Main.get_config('enabled_stages')]:
            staticdb[s.stagename] = self._full_path("static-analysis-%s.h5" % s.stagename)
            staticdb_done[s.stagename] = self._full_path("static-analysis-%s.completed"
                                                         % s.stagename)
        Main.set_config("staticdb", lambda x: staticdb[x.stagename])
        Main.set_config("staticdb_done", lambda x: staticdb_done[x.stagename])

        # calculate thumb ranges on demand
        def get_thumb_ranges(stage):
            n = stage.stagename
            flatkey = "%s_%s" % ("thumb_ranges", n)
            v = Main.get_config(flatkey)
            if v is None:
                # calculate and add
                v = staticanalysis.ThumbRanges.find_thumb_ranges(stage)
                Main.set_config(flatkey, v)
            return v

        Main.set_config("thumb_ranges", get_thumb_ranges)

        # calculate labels on demand
        def get_labels():
            internal = "labels_internal"
            v = Main.get_config(internal)
            if v is None:
                tmpdir = Main.get_config("temp_bootloader_src_dir")
                olddir = os.getcwd()
                os.chdir(tmpdir)
                v = labeltool.get_all_labels(tmpdir)
                Main.set_config(internal, v)
                os.chdir(olddir)
            return v
        if not Main.get_config("labels"):
            Main.set_config("labels", get_labels)
        for s in [Main.stage_from_name(st) for st in Main.get_config('enabled_stages')]:
            n = s.stagename
            target = staticdb[n]
            done_target = staticdb_done[n]

            class run_analysis():
                def __init__(self, stage):
                    self.stage = stage

                def __call__(self):
                    target = Main.get_config("staticdb", self.stage)
                    if os.path.exists(target):
                        os.remove(target)
                    done_target = Main.get_config('staticdb_done', self.stage)
                    db_info.create(self.stage, "staticdb")
                    return os.system("touch %s" % done_target) == 0
            a = DelTargetAction(run_analysis(s))
            actions = [a]
            rtask = ActionListTask(actions,
                                   [Main.get_config("stage_elf", s)],
                                   [target, done_target], "staticanalysis_%s" % n)
            tasks.append(rtask)

        return tasks

    def _image_tasks(self):
        tasks = []
        bootimages = []
        bootelfs = []
        imgsrcs = []
        elfdst = {}
        imgdst = {}
        deps = []
        targets = []
        tasks.append(self._mkdir(self.test_data_path))
        tasks.append(self._mkdir(self._full_path()))
        Main.set_config("test_instance_root", self._full_path())
        dstdir = self._full_path("images")
        tasks.append(self._mkdir(dstdir))
        self.config_path = self._full_path("config.yml")
        Main.set_config("instance_config_file", self.config_path)

        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                settings = yaml.load(f)
                self.local = settings['local']
                self.sha = settings['sha1']
        else:
            self.local = self.gitinfo['local']
            self.sha = self.gitinfo['sha1']

        def write(f, local, sha):
            with open(f, "w") as fconfig:
                contents = """
local: {}
sha1: {}
"""
                filecontents = contents.format(local, sha)
                fconfig.write(filecontents)
        a = ActionListTask([(write, [self.config_path, self.local, self.sha])],
                           [], [self.config_path], "instance_config_file")
        tasks.append(a)

        Main.set_config("instance_git_local", self.local)
        Main.set_config("instance_git_sha", self.sha)
        # make temporary copy of git tree to pull labels from
        tmpdir = Main.get_config("temp_bootloader_src_dir")
        if not tmpdir:
            tmpdir = tempfile.mkdtemp()
            Main.set_config("temp_bootloader_src_dir", tmpdir)
            def rm_src_dir():
                print "removing temporary copy of bootloader source code at %s" % tmpdir
                os.system("rm -rf %s" % tmpdir)
            if self.rm_tmp:
                atexit.register(rm_src_dir)
        olddir = os.getcwd()
        os.chdir(self.local)
        os.system("git archive %s | tar -C %s -x" % (self.sha, tmpdir))
        os.chdir(olddir)
        for i in self.boot_stages:
            broot = Main.get_bootloader_cfg().software_cfg.root
            base = re.sub(broot+"/", "", i.elf)
            bootelfs.append(os.path.join(tmpdir, base))
            base = re.sub(broot+"/", "", i.image)
            bootimages.append(os.path.join(tmpdir, base))
            elfdst[i.stagename] = os.path.join(dstdir, os.path.basename(i.elf))
            imgdst[i.stagename] = os.path.join(dstdir, os.path.basename(i.image))
        targets.extend(elfdst.values())
        Main.set_config("stage_elf", lambda s: elfdst[s.stagename])
        Main.set_config("stage_image", lambda s: imgdst[s.stagename])
        tocpy = bootelfs + bootimages
        build_cmds = []
        builder = self.manager.build([Main.get_bootloader_cfg().software], False)[0]
        for t in builder.tasks:
            for action in t.list_tasks()['actions']:
                if isinstance(action, CmdAction):
                    do = action.expand_action()
                    build_cmds.append("cd %s && %s" % (tmpdir, do))
        build_cmds.append("mkdir -p %s && true" % dstdir)
        for i in tocpy:
            build_cmds.append("cp %s %s" % (i, dstdir))
        sdtarget = os.path.join(dstdir, "sd.img")
        sdskeleton = self.hardwareclass.sdskeleton
        sdtmpdir = tempfile.mkdtemp()
        tmpmnt = os.path.join(sdtmpdir, "mnt")
        tmpsd = os.path.join(sdtmpdir, "sd.img")
        cp = "cp %s %s" % (sdskeleton, tmpsd)
        mkdir = "mkdir -p %s" % (tmpmnt)
        mnt = "sudo mount -o loop,offset=%d %s %s" % (512*63, tmpsd, tmpmnt)
        update_mnt = []
        for i in imgdst.itervalues():
            update_mnt.append("sudo cp %s %s" % (i,
                                                 tmpmnt))
        umount = "sudo umount %s" % tmpmnt
        cp_final = "cp %s %s" % (tmpsd, sdtarget)
        rmtmp = "sudo rm -r %s" % sdtmpdir
        cmds = build_cmds + [cp, mkdir, mnt] + update_mnt + [umount, cp_final, rmtmp]
        Main.set_config('sd_image', sdtarget)
        targets.append(sdtarget)
        mksd = CmdTask(cmds,
                       [],
                       targets,
                       "sd_card_image")

        if self.create:
            tasks.append(mksd)
            mksd.uptodate = [False]
        elif os.path.exists(sdtarget):
            mksd.uptodate = [True]
        else:
            tasks.append(mksd)
        if 'all' in self.enabled_stages or self.enabled_stages is None:
            Main.set_config('enabled_stages',
                            [s.stagename for
                             s in list(Main.get_bootloader_cfg().supported_stages.itervalues())])
        else:
            ss = [v if isinstance(v, str) else v.stagename for v in
                  Main.get_bootloader_cfg().supported_stages.itervalues()
                  if v.stagename in self.enabled_stages]
            Main.set_config('enabled_stages', ss)
        for stage in Main.get_bootloader_cfg().supported_stages.itervalues():
            for t in ["elf", "image"]:
                if t == "elf":
                    e = elfdst[stage.stagename]
                else:
                    e = imgdst[stage.stagename]
                if os.path.exists(e):
                    setattr(stage, t, e)
        for s in self.boot_stages:
            if os.path.exists(Main.get_config('stage_elf', s)):
                s.post_build_setup()

        return tasks


class PolicyTaskLoader(ResultsLoader):
    def __init__(self, policies):
        test_id = Main.get_config("test_instance_id")
        super(PolicyTaskLoader, self).__init__(test_id, "policy", True)
        self.policies = policies
        self.instance_dir = Main.get_config("test_instance_root")
        self.task_adders = [self._policy_tasks]
        self._add_tasks()

    def _policy_root(self, rel=""):
        return os.path.join(self.instance_dir, 'policies', rel)

    def _full_path(self, stage, rel=""):
        return os.path.join(self._policy_root(), stage.stagename, rel)

    def _policy_tasks(self):
        tasks = []
        policies = {}
        regions = {}
        names = {}
        dbs = {}
        dbs_done = {}
        stages_with_policies = []
        pname = "substages.yml"
        rname = "memory_map.yml"
        tasks.append(self._mkdir(self._policy_root()))
        for s in [Main.stage_from_name(st) for st in Main.get_config('enabled_stages')]:
            s_policy = None
            s_regions = None
            n = s.stagename
            policy_file_name = "substages-%s.yml" % n
            regions_file_name = "memory_map-%s.yml" % n
            policystagedir = self._full_path(s)
            tasks.append(self._mkdir(policystagedir))
            stages_with_policies.append(s)
            if n not in self.policies:
                policy_entry = None
            else:
                policy_entry = self.policies[n]
            if type(policy_entry) == str:
                substagedatadir = os.path.join(policystagedir, policy_entry)
                s_policy = os.path.join(substagedatadir, policy_file_name)
                s_regions = os.path.join(substagedatadir, regions_file_name)
            elif not policy_entry:
                # choose a default
                policy_dir = self._full_path(s)
                # choose any file in dir
                choices = glob.glob(policy_dir + "/*")
                for c in choices:
                    d = os.path.basename(c)
                    if d.startswith("."):
                        continue
                    if os.path.isdir(c):
                        s_policy = os.path.join(c, pname)
                        s_regions = os.path.join(c, rname)
                        if os.path.isfile(s_policy) and \
                           os.path.isfile(s_regions):
                            break
            else:
                s_policy = policy_entry[0]  # self.policies[n][0]
                s_regions = policy_entry[1]  # self.policies[n][1]

            pdir = os.path.join(Main.get_config("bootloader_data_dir"), n)
            if s_policy is None or not os.path.exists(s_policy):  # use default
                s_policy = os.path.join(pdir, pname)
            if s_regions is None or not os.path.exists(s_regions):  # use default
                s_regions = os.path.join(pdir, rname)
            names[n] = substage.SubstagesInfo.calculate_name_from_files(s_policy, s_regions)
            datadir = os.path.join(policystagedir, names[n])
            policy_instance_dir = datadir
            policies[n] = os.path.join(policy_instance_dir, policy_file_name)
            regions[n] = os.path.join(policy_instance_dir, regions_file_name)
            dbs_done[n] = os.path.join(policy_instance_dir, "policy-%s.completed" % n)
            dbs[n] = os.path.join(policy_instance_dir, "policy-%s.h5" % n)
            tasks.append(self._mkdir(policy_instance_dir))

            c = self._copy_file(s_policy,
                                policies[n])
            # c.task_dep.append(policy_instance_dir)
            tasks.append(c)
            c.file_dep.append(s_regions)
            c.file_dep.append(s_policy)
            c = self._copy_file(s_regions,
                                regions[n])
            # c.task_dep = [policy_instance_dir]
            tasks.append(c)
        Main.set_config("policy_file", lambda s: policies[s.stagename])
        Main.set_config("regions_file", lambda s: regions[s.stagename])
        Main.set_config("policy_name", lambda s: names[s.stagename])
        Main.set_config("policy_db", lambda s: dbs[s.stagename])
        Main.set_config("policy_db_done", lambda s: dbs_done[s.stagename])
        Main.set_config("stages_with_policies", stages_with_policies)

        for s in Main.get_config("stages_with_policies"):
            n = s.stagename

            class setup_policy():
                def __init__(self, stage):
                    self.stage = stage

                def __call__(self):
                    target = Main.get_config("policy_db", self.stage)
                    if os.path.exists(target):
                        os.remove(target)
                    d = Main.get_config("policy_db_done", self.stage)
                    db_info.create(self.stage, "policydb", trace=False)
                    os.system("touch %s" % d)
            pdb_done = Main.get_config("policy_db_done", s)
            target = Main.get_config("policy_db", s)
            at = DelTargetAction(setup_policy(s))
            actions = [at]
            staticdb_done = Main.get_config("staticdb_done", s)
            staticdb = Main.get_config("staticdb", s)
            mmapdb_done = Main.get_config("mmapdb_done")
            mmapdb = Main.get_config("mmapdb")
            targets = []
            if n in self.policies:
                targets = [target, pdb_done]

            a = ActionListTask(actions,
                               [mmapdb_done, mmapdb, staticdb_done,
                                staticdb, policies[n], regions[n]],
                               targets,
                               "create_%s_policy_db" % n)
            # a.task_dep.append(os.path.dirname(pdb_done))
            # a.task_dep.append(os.path.dirname(target))
            a.task_dep.append("instance")
            tasks.append(a)
        return tasks
