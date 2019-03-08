#!/usr/bin/env python
##
## This file is part of OpenSIPS CLI
## (see https://github.com/OpenSIPS/opensips-cli).
##
## This program is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program. If not, see <http://www.gnu.org/licenses/>.
##

import cmd
import sys
import os
import shlex
import readline
import atexit
import importlib
from opensipscli import comm
from opensipscli import defaults
from opensipscli.config import cfg
from opensipscli.logger import logger

class OpenSIPSCLIShell(cmd.Cmd, object):

    modules = {}
    registered_atexit = False

    def __init__(self, options):

        self.debug = options.debug
        self.execute = options.execute
        self.command = options.command
        self.modules_dir_inserted = None

        if self.debug:
            logger.setLevel("DEBUG")

        if not options.config:
            cfg_file = None
            for f in defaults.CFG_PATHS:
                if os.path.isfile(f) and os.access(f, os.R_OK):
                    # found a valid config file
                    cfg_file = f
                    break
        else:
            cfg_file = options.config
        if not cfg_file:
            logger.debug("no config file found in any of {}".
                    format(", ".join(defaults.CFG_PATHS)))
        else:
            logger.debug("using config file {}".format(cfg_file))

        # __init__ of the configuration file
        cfg.parse(cfg_file)
        if not cfg.has_instance(options.instance):
            logger.warning("Unknown instance '{}'! Using default instance '{}'!".
                    format(options.instance, defaults.DEFAULT_SECTION))
            instance = defaults.DEFAULT_SECTION
        else:
            instance = options.instance
        cfg.set_instance(instance)
        cfg.set_custom_options(options.extra_options)

        if not self.execute:
            # __init__ of cmd.Cmd module
            cmd.Cmd.__init__(self)
            # Clear the modules and commands list
            for mod in ['clear', 'help', 'history', 'exit', 'quit']:
                self.modules[mod] = (self, None)

        # Opening the current working instance
        self.update_instance(cfg.current_instance)

        if not cfg.exists('skip_modules'):
            skip_modules = []
        else:
            skip_modules = cfg.get('skip_modules')

        # load all modules from the 'modules_dir'
        for fname in os.listdir(self.modules_dir_inserted):
            if os.path.isfile(fname) or not fname.endswith(".py"):
                continue
            module = fname[:-3]
            if fname in skip_modules:
                logger.debug("Skipping module '{}'".format(module))
                continue
            m = importlib.import_module(module)
            if not hasattr(m, module):
                logger.debug("Skipping module '{}' - module implementation not found".
                        format(module))
                continue
            mod = getattr(m, module)
            if not hasattr(mod, '__exclude__') or not hasattr(mod, '__get_methods__'):
                logger.debug("Skipping module '{}' - module does not implement Module".
                        format(module))
                continue
            if mod.__exclude__(mod):
                logger.debug("Skipping module '{}' - excluded on purpose".format(module))
                continue
            logger.debug("Loaded module '{}'".format(module))
            self.modules[module] = (mod(), mod.__get_methods__(mod))

    def update_logger(self):

        # first of all, let's handle logging
        if self.debug:
            level = "DEBUG"
        else:
            level = cfg.get("log_level")
        logger.setLevel(level)

    def clear_instance(self):
        # make sure we dump everything before swapping files
        self.history_write()
        if self.modules_dir_inserted:
            sys.path.remove(self.modules_dir_inserted)
            self.modules_dir_inserted = None

    def update_instance(self, instance):

        # first of all, let's handle logging
        self.current_instance = instance
        self.update_logger()

        # Update the intro and prompt
        self.intro = cfg.get('prompt_intro')
        self.prompt = '(%s): ' % cfg.get('prompt_name')

        # add modules_dir to the path
        modules_dir = cfg.get('modules_dir')
        if not os.path.exists(modules_dir):
            logger.warning("Modules dir '{}' does not exist!".
                    format(modules_dir))
        elif not modules_dir in sys.path:
            sys.path.insert(0, modules_dir)
            self.modules_dir_inserted = modules_dir

        # initialize communcation handler
        self.handler = comm.initialize()

    def history_write(self):
        history_file = cfg.get('history_file')
        logger.debug("saving history in {}".format(history_file))
        readline.write_history_file(history_file)

    def preloop(self):
        history_file = cfg.get('history_file')
        if readline and os.path.exists(history_file):
            readline.read_history_file(history_file)
            logger.debug("using history file {}".format(history_file))
        readline.set_history_length(int(cfg.get('history_file_size')))
        if not self.registered_atexit:
            atexit.register(self.history_write)

    def postcmd(self, stop, line):

        if self.current_instance != cfg.current_instance:
            self.clear_instance()
            self.update_instance(cfg.current_instance)
            # make sure we update all the history information
            self.preloop()

        return stop

    # Overwritten funtion in order not to print misc commands
    def print_topics(self, header, cmds, cmdlen, maxcol):
        if header is not None:
            if cmds:
                self.stdout.write('%s\n' % str(header))
                if self.ruler:
                    self.stdout.write('%s\n' % str(self.ruler*len(header)))
                self.columnize(cmds, maxcol-1)
                self.stdout.write('\n')

    # Overwritten function in order to catch SIGINT
    def cmdloop(self, intro=None):
        if self.execute:
            ret = -1
            if len(self.command) < 1:
                logger.error("no modules to run specified!")
            elif len(self.command) < 2:
                logger.error("no method to in '{}' run specified!".
                        format(self.command[0]))
            else:
                logger.debug("running in non-interactive mode '{}'".format(self.command))
                ret = self.run_command(self.command[0], self.command[1], self.command[2:])
                # assume that by default it exists with success
                if ret is None:
                    ret = 0
            return ret
        print(self.intro)
        while True:
            try:
                super(OpenSIPSCLIShell, self).cmdloop(intro='')
                break
            except KeyboardInterrupt:
                print('^C')
                return 0
        # any other commands exits with negative value
        return -1

    def complete_modules(self, text):
        l = [a for a in self.modules.keys() if a.startswith(text)]
        if len(l) == 1:
            l[0] = l[0] + " "
        return l

    def complete_functions(self, module, text, line, begidx, endidx):

        # builtin commands
        params = line.split()
        if len(params) < 2 or len(params) == 2 and line[-1] != ' ':
            # still looking for a module's command
            if module[1] is None or len(module[1]) == 0:
                return ['']
            l = [a for a in module[1] if a.startswith(text)]
            if len(l) == 1:
                l[0] += " "
        else:
            try:
                compfunc = getattr(module[0], '__complete__')
                l = module[0].__complete__(params[1], text, line, begidx, endidx)
                if not l:
                    return None
            except AttributeError:
                return ['']
            # looking for a different command
        return l

    # Overwritten function for our customized auto-complete
    def complete(self, text, state):
        if state == 0:
            origline = readline.get_line_buffer()
            line = origline.lstrip()
            stripped = len(origline) - len(line)
            begidx = readline.get_begidx() - stripped
            endidx = readline.get_endidx() - stripped
            if begidx > 0:
                mod, args, foo = self.parseline(line)
                if mod == '':
                    return self.complete_modules(text)[state]
                elif not mod in self.modules:
                    logger.error("BUG: mod '{}' not found!".format(mod))
                else:
                    module = self.modules[mod]
                    self.completion_matches = \
                        self.complete_functions(module, text, line, begidx, endidx)
            else:
                self.completion_matches = self.complete_modules(text)
        try:
            return self.completion_matches[state]
        except IndexError:
            return ['']

    # Execute commands from Modules
    def run_command(self, module, cmd, params):
        try:
            mod = self.modules[module]
        except (AttributeError, KeyError):
            logger.error("no module '{}' loaded".format(module))
            return
        if not cmd in mod[1]:
            logger.error("no command '{}' in module '{}'".
                    format(cmd, module))
            return
        logger.debug("running command '{}' '{}'".format(cmd, params))
        return mod[0].__invoke__(cmd, params)

    def default(self, line):
        aux = shlex.split(line)
        if len(aux) < 2:
            logger.error("imcomplete command '{}'".format(line))
            return
        module = str(aux[0])
        cmd = str(aux[1])
        params = aux[2:]
        self.run_command(module, cmd, params)

    # Print history
    def do_history(self, line):
        if not line:
            with open(cfg.get('history_file')) as hf:
                for num, line in enumerate(hf, 1):
                    print(num, line, end='')

    # Used to get info for a certain command
    def do_help(self, line):
        # TODO: Add help for commands
        print("Usage:: help cmd - returns information about \"cmd\"")

    # Clear the terminal screen
    def do_clear(self, line):
        os.system('clear')

    # Commands used to exit the shell
    def do_EOF(self, line):  # It catches Ctrl+D
        print('^D')
        return True

    def do_quit(self, line):
        return True

    def do_exit(self, line):
        return True
