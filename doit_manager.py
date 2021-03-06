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

import external_source_manager
import instrumentation_results_manager
from doit.cmd_base import ModuleTaskLoader, TaskLoader
from doit.doit_cmd import DoitMain
from doit.cmd_base import Command
from doit.action import CmdAction
from config import Main
import pure_utils
import sys
import os
import glob
import difflib
import yaml


class TaskManager():
    loaders = []
    tasks = {}

    def __init__(self, do_build, create_test, enabled_stages,
                 policies, quick, run_trace, select_trace, import_policies,
                 post_trace_processing=[], open_instance=None, run=True,
                 print_cmds=False, hook=False, rm_dir=True):
        (print_build_cmd, build_source) = ([], [])
        bootloader = Main.get_bootloader_cfg()
        self.create_test = create_test
        self.print_cmds = print_cmds
        self.src_manager = external_source_manager.SourceLoader(print_build_cmd, build_source)
        self.loaders.append(self.src_manager)
        if (len(print_build_cmd) + len(build_source) > 0):
            return

        self.boot_task = [s for s in self.src_manager.code_tasks
                          if s.build_cfg.name == bootloader.software][0]
        needs_build = False
        if create_test:
            if not self.boot_task.has_nothing_to_commit():
                self.boot_task.commit_changes()
                needs_build = True
            (self.test_id, gitinfo) = self._calculate_current_id()
            current_id = self.test_id
            run = True
        else:
            if open_instance is None:
                self.test_id = self._get_newest_id()
            else:
                ids = self._get_all_ids()
                if open_instance not in ids:
                    open_instance = difflib.get_close_matches(open_instance,
                                                              ids, 1, 0)[0]
                    open_instance = os.path.basename(open_instance)
                self.test_id = open_instance
            # resolve trace name to something that exists
            select_trace = instrumentation_results_manager.TraceTaskPrepLoader.get_trace_name(self.test_id,
                                                                                              select_trace)
            config_path = os.path.join(instrumentation_results_manager.TraceTaskPrepLoader.test_path(self.test_id,
                                                                                                     select_trace),
                                       "config.yml")
            print config_path
            with open(config_path, 'r') as f:
                settings = yaml.load(f)
                enabled_stages = settings['stages']
            self.boot_task.build.uptodate = [True]
            (current_id, gitinfo) = self._calculate_current_id()
            run = False

        self.ti = instrumentation_results_manager.InstrumentationTaskLoader(self.boot_task,
                                                                            self.test_id,
                                                                            enabled_stages,
                                                                            run,
                                                                            gitinfo,
                                                                            self,
                                                                            needs_build, rm_dir)

        if create_test:
            self.ppt = instrumentation_results_manager.PolicyTaskLoader(policies)
            self.loaders.append(instrumentation_results_manager.task_manager())
            return

        self.tp = instrumentation_results_manager.TraceTaskPrepLoader(run_trace,
                                                                      select_trace,
                                                                      not hook and len(post_trace_processing) == 0,
                                                                      run,
                                                                      self.print_cmds,
                                                                      create_test,
                                                                      hook)

        self.pt = instrumentation_results_manager.PolicyTaskLoader(policies)
        run = run and (len(post_trace_processing) == 0)
        self.rt = instrumentation_results_manager.TraceTaskLoader(self.tp.stages,
                                                                  self.tp.hw,
                                                                  self.tp.tracenames,
                                                                  self.tp.trace_id,
                                                                  not self.print_cmds,
                                                                  quick,
                                                                  run,
                                                                  self.print_cmds,
                                                                  self.tp.new)
        run = (not create_test) or len(post_trace_processing) > 0
        self.ppt = instrumentation_results_manager.PostTraceLoader(post_trace_processing, run)
        self.loaders.append(instrumentation_results_manager.task_manager())

    def _get_all_ids(self):
        root = Main.test_data_path
        return glob.glob(root + "/*")

    def _get_newest_id(self):
        choices = self._get_all_ids()
        newest = None
        newest_time = 0
        for i in choices:
            itime = os.stat(i).st_ctime
            if itime > newest_time:
                newest = i
                newest_time = itime
        n = os.path.basename(newest)
        return n

    def _calculate_current_id(self):
        (gitid,  sha) = self.boot_task.get_gitinfo()
        cc = Main.cc
        cc_name = self.boot_task.build_cfg.compiler_name
        ccpath = "%s%s" % (cc, cc_name)
        defconfig = Main.get_bootloader_cfg().makecfg
        hwclass = Main.get_hardwareclass_config().name
        bootsoftware = self.boot_task.build_cfg.name
        ccinfo = pure_utils.file_md5(ccpath)
        gitinfo = {'local': self.boot_task.build_cfg.root,
                   'sha1': sha}
        return ("%s.%s.%s.%s.%s" % (hwclass, bootsoftware, defconfig, gitid, ccinfo),
                gitinfo)

    def build(self, targets, do_build=True):
        if do_build:
            makes = [external_source_manager.CodeTask.get_task_name(b, "build") for b in targets]
            return self.run(makes)
        else:
            rets = []
            for t in self.src_manager.code_tasks:
                if t.basename in targets:
                    rets.append(t)
            return rets

    def run(self, cmds):
        tasks = {}
        for v in self.loaders:
            for name, l in v.list_tasks():

                f = l
                tasks[name] = f
        ml = ModuleTaskLoader(tasks)
        main = DoitMain(ml)
        main.config['default_tasks'] = cmds
        main.config['verbose'] = 2
        main.config['verbosity'] = 2
        return main.run([])

    def create_test_instance(self):
        nm = self.ti.get_build_name()
        print "about to run %s" % nm
        ret = self.run([nm])
        return ret

    def run_trace(self):
        if self.print_cmds:
            return 0
        nm = self.rt.get_build_name()
        print "about to run %s" % nm
        ret = self.run([nm])
        return ret

    def postprocess_trace(self):
        nm = self.ppt.get_build_name()
        print "about to run %s" % nm
        ret = self.run([nm])
        return ret
