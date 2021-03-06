#!/usr/bin/env python2
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
from doit.action import CmdAction

import argparse
import os
import run_cmd
import sys
import doit_manager
import instrumentation_results_manager


if __name__ == '__main__':
    parser = argparse.ArgumentParser("Bootloader test suite")
    cmds = parser.add_mutually_exclusive_group()
    cmds.add_argument('-c', '--create',
                      help='Create new results direcory, deleting any existing data if "\
                      "(-o specifies an existing directory)',
                      action='store_true', default=False)
    parser.add_argument('-q', '--quick',
                        help='Try to skip some steps to be faster',
                        action='store_true', default=False)
    cmds.add_argument('-B', '--build',
                      help="Name of software to clean, update git tree, "
                      "and build (openocd, u-boot, qemu))",
                      action="append", default=[])
    cmds.add_argument('-b', '--buildcommands',
                      help="Print build commands for the listed software "
                      "(openocd, u-boot, qemu))", default=[],
                      action="append")
    cmds.add_argument('--print_policy', default=False, action='store_true')
    parser.add_argument('-o', '--testcfginstance',
                        help='Name of test config result directory to open, " \
                        "by default we use newest',
                        action='store', default=None)
    parser.add_argument('-S', '--enabled_stages', action='append', default=['spl'])

    class TraceAction(argparse.Action):
        def __init__(self, option_strings, dest, **kwargs):
            self.stages = list(Main.get_bootloader_cfg().supported_stages.itervalues())
            hw_classes = Main.get_hardwareclass_config().hardware_type_cfgs
            self.hw_classes = list(hw_classes.iterkeys())
            self.tracing_methods = {k: v.tracing_methods for k, v in hw_classes.iteritems()}
            self.nargs = 3
            self.selected = False
            self.d = {'stages': ["spl"],
                      "traces": ["breakpoint", "calltrace"],
                      "hw": "bbxmqemu",
            }

            kwargs['default'] = {}
            super(TraceAction, self).__init__(option_strings, dest, **kwargs)

        def __call__(self, parser, namespace, values, option_string=None):
            if len(values) >= 3:
                stagename = values[0]
                hw = values[1]
                traces = values[2:]
            else:
                hw = self.d["hw"]
                traces = self.d["traces"]
                stagename = self.d["stages"]
                # typ = self.d["type"]
            stagenames = [s.stagename for s in self.stages]
            if (stagename not in stagenames) and (not stagename == "all"):
                raise argparse.ArgumentError(self,
                                             "%s not a valid stage, must be one of %s" %
                                             (stagename, stagenames))
            if hw not in self.hw_classes:
                raise argparse.ArgumentError(self,
                                             "%s not a valid hardware name, must be one of %s" %
                                             (hw, str(self.hw_classes)))
            for trace in traces:
                if trace not in self.tracing_methods[hw]:
                    raise argparse.ArgumentError(self,
                                                 "%s not a valid tracing method, "
                                                 "must be one of %s" %
                                                 (trace, str(self.tracing_methods[hw])))
            if stagename == "all":
                stages = [s.stagename for s in self.stages]
            else:
                stages = [stagename]
            setattr(namespace, self.dest, {'stages': stages,
                                           'hw': hw,
                                           'traces': traces})

    class SubstageFileAction(argparse.Action):
        def __init__(self, option_strings, dest, **kwargs):
            self.stages = list(Main.get_bootloader_cfg().supported_stages.itervalues())
            self.stagenames = [s.stagename for s in self.stages]
            self.nargs = 3
            defaultdir = os.path.join(Main.hw_info_path,
                                      Main.get_hardwareclass_config().name,
                                      Main.get_bootloader_cfg().software)
            self.sdefaults = {s.stagename: (os.path.join(defaultdir, s.stagename, "substages.yml"),
                                            os.path.join(defaultdir, s.stagename, "memory_map.yml"))

                              for s in self.stages}
            if dest == "importpolicy":
                defaults = self.sdefaults
            else:
                defaults = {}

            kwargs['default'] = defaults
            super(SubstageFileAction, self).__init__(option_strings, dest, **kwargs)

        def __call__(self, parser, namespace, values, option_string=None):
            stagename = values[0]
            f = os.path.abspath(values[1])
            d = os.path.abspath(values[2])
            if stagename not in self.stagenames:
                raise argparse.ArgumentError(self,
                                             "%s not a valid stage, must be one of %s" %
                                             (stagename, str(self.stagenames)))
            if self.dest == 'importpolicy':
                setattr(namespace, "doimport", True)
            for s in self.stagenames:
                if s == stagename:
                    getattr(namespace, self.dest)[s] = (f, d)
                else:
                    if s not in getattr(namespace, self.dest).iterkeys():
                        getattr(namespace, self.dest)[s] = self.sdefaults[s]

    class SubstageNameAction(argparse.Action):
        def __init__(self, option_strings, dest, **kwargs):
            stages = list(Main.get_bootloader_cfg().supported_stages.itervalues())
            self.stagenames = [s.stagename for s in stages]
            self.nargs = 2
            defaults = {}
            kwargs['default'] = defaults
            super(SubstageNameAction, self).__init__(option_strings, dest, **kwargs)

        def __call__(self, parser, namespace, values, option_string=None):
            stagename = values[0]
            substages_name = values[1]
            if stagename not in self.stagenames:
                raise argparse.ArgumentError(self,
                                             "%s not a valid stage, must be one of %s" %
                                             (stagename, str(self.stagenames)))
            getattr(namespace, self.dest)[stagename] = substages_name
    parser.add_argument('-k', '--keep_temp_files', action="store_true")
    cmds.add_argument('-I', '--importpolicy',
                      help="Stage name, path to file containing proposed substages, "
                      "and path to file containing region info",
                      action=SubstageFileAction, nargs=3)
    parser.add_argument('-P', '--policyfiles',
                        help="Stage name, path to file containing proposed substages, "
                        "and path to file containing region info",
                        action=SubstageFileAction, nargs=3)
    parser.add_argument('-n', '--policyname', action=SubstageNameAction, nargs=2)
    cmds.add_argument('-r', '--run_trace', action=TraceAction, nargs="*",
                      help="run new trace and collect data for specified stage "
                      "<stage>, hardware <hw>, tracing method <trace> "
                      "(-r <type> <stage> <hw> <trace>")
    parser.add_argument('-t', '--select_trace', default=None, action="store",
                        help="Select existing trace by name")
    cmds.add_argument('-T', '--postprocess_trace', default=[], action="append",
                      choices=instrumentation_results_manager.PostTraceLoader.supported_types,
                      help="Run trace postprocessing command")
    parser.add_argument('-p', '--print_cmds', action='store_true',
                        help='Print commands instead of running them, only works with -t')

    args = parser.parse_args()
    for l in ('enabled_stages',):  # , 'enabled_hardware'):
        if len(getattr(args, l)) == 0:
            setattr(args, l, ['all'])

    shell = run_cmd.Cmd()
    res = 0
    do_build = True if args.build or args.buildcommands else False
    run = True
    import_policies = False
    if args.create:
        policies = args.policyfiles
        import_policies = True
    elif hasattr(args, "doimport"):
        policies = args.importpolicy
        import_policies = True
    elif args.policyname:
        policies = args.policyname
    else:
        policies = args.importpolicy

    if args.print_cmds or args.importpolicy:
        run = False
    if args.run_trace:
        args.enabled_stages = args.run_trace['stages']
    task_mgr = doit_manager.TaskManager((args.buildcommands, args.build),
                                        args.create,
                                        args.enabled_stages,
                                        policies,
                                        args.quick,
                                        args.run_trace,
                                        args.select_trace,
                                        import_policies,
                                        args.postprocess_trace,
                                        args.testcfginstance, run,
                                        args.print_cmds,
                                        rm_dir=not args.keep_temp_files)

    if args.create or import_policies or args.print_cmds:
        task_mgr.create_test_instance()
    elif args.run_trace:
        task_mgr.run_trace()
    elif args.postprocess_trace:
        task_mgr.postprocess_trace()
    if args.print_cmds:
        task_mgr.rt.do_print_cmds()
    #if args.buildcommands or
    if args.print_cmds or (args.build and not args.create):
        targets = args.build if args.build else args.buildcommands
        ret = task_mgr.build(targets, True if args.build else False)
        if args.buildcommands:
            for r in ret:
                for task in r.tasks:
                    print "to %s %s:" % (task.name, task.basename)
                    for action in task.list_tasks()['actions']:
                        if isinstance(action, CmdAction):
                            print "cd %s" % task.root_dir
                            print action.expand_action()
                    print "\n"
