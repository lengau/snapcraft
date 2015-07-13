# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2015 Canonical Ltd
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import glob
import importlib
import os
import snapcraft
import snapcraft.common
import sys
import yaml


class Plugin:

    def __init__(self, name, partName, properties, optionsOverride=None, loadCode=True, loadConfig=True):
        self.valid = False
        self.code = None
        self.config = None
        self.partNames = []
        self.deps = []
        self.pluginName = name
        self.isLocalPlugin = False

        self.sourcedir = os.path.join(os.getcwd(), "parts", partName, "src")
        self.builddir = os.path.join(os.getcwd(), "parts", partName, "build")
        self.installdir = os.path.join(os.getcwd(), "parts", partName, "install")
        self.stagedir = os.path.join(os.getcwd(), "stage")
        self.snapdir = os.path.join(os.getcwd(), "snap")
        self.statefile = os.path.join(os.getcwd(), "parts", partName, "state")

        if loadConfig:
            # First look in local path
            localPluginDir = os.path.abspath(os.path.join('parts', 'plugins'))
            configPath = os.path.join(localPluginDir, name + ".yaml")
            if os.path.exists(configPath):
                self.isLocalPlugin = True
            else:
                # OK, now look at snapcraft's plugins
                configPath = os.path.join(snapcraft.common.plugindir, name + ".yaml")
                if not os.path.exists(configPath):
                    snapcraft.common.log("Unknown plugin %s" % name, file=sys.stderr)
                    return
            with open(configPath, 'r') as fp:
                self.config = yaml.load(fp) or {}

            if loadCode:
                class Options():
                    pass
                options = Options()

                if self.config:
                    for opt in self.config.get('options', []):
                        if opt in properties:
                            setattr(options, opt, properties[opt])
                        else:
                            if self.config['options'][opt].get('required', False):
                                snapcraft.common.log("Required field %s missing on part %s" % (opt, name), file=sys.stderr)
                                return
                            setattr(options, opt, None)
                if optionsOverride:
                    options = optionsOverride

                moduleName = self.config.get('module', name)

                # Load code from local plugin dir if it is there
                if self.isLocalPlugin:
                    sys.path = [localPluginDir] + sys.path
                else:
                    moduleName = 'snapcraft.plugins.' + moduleName

                module = importlib.import_module(moduleName)

                if self.isLocalPlugin:
                    sys.path.pop(0)

                for propName in dir(module):
                    prop = getattr(module, propName)
                    if issubclass(prop, snapcraft.BasePlugin):
                        self.code = prop(partName, options)
                        break

        self.partNames.append(partName)
        self.valid = True

    def __str__(self):
        return self.partNames[0]

    def __repr__(self):
        return self.partNames[0]

    def makedirs(self):
        try:
            os.makedirs(self.sourcedir)
        except FileExistsError:
            pass
        try:
            os.makedirs(self.builddir)
        except FileExistsError:
            pass
        try:
            os.makedirs(self.installdir)
        except FileExistsError:
            pass
        try:
            os.makedirs(self.stagedir)
        except FileExistsError:
            pass
        try:
            os.makedirs(self.snapdir)
        except FileExistsError:
            pass

    def isValid(self):
        return self.valid

    def names(self):
        return self.partNames

    def notifyStage(self, stage, hint=''):
        snapcraft.common.log(stage + " " + self.partNames[0] + hint)

    def isDirty(self, stage):
        try:
            with open(self.statefile, 'r') as f:
                lastStep = f.read()
                return snapcraft.common.commandOrder.index(stage) > snapcraft.common.commandOrder.index(lastStep)
        except Exception:
            return True

    def shouldStageRun(self, stage, force):
        if not force and not self.isDirty(stage):
            self.notifyStage('Skipping ' + stage, ' (already ran)')
            return False
        return True

    def markDone(self, stage):
        with open(self.statefile, 'w+') as f:
            f.write(stage)

    def pull(self, force=False):
        if not self.shouldStageRun('pull', force):
            return True
        self.makedirs()
        if self.code and hasattr(self.code, 'pull'):
            self.notifyStage("Pulling")
            if not getattr(self.code, 'pull')():
                return False
            self.markDone('pull')
        return True

    def build(self, force=False):
        if not self.shouldStageRun('build', force):
            return True
        self.makedirs()
        if self.code and hasattr(self.code, 'build'):
            self.notifyStage("Building")
            if not getattr(self.code, 'build')():
                return False
            self.markDone('build')
        return True

    def stage(self, force=False):
        if not self.shouldStageRun('stage', force):
            return True
        self.makedirs()
        if not self.code:
            return True

        self.notifyStage("Staging")
        snapcraft.common.run(['cp', '-arT', self.installdir, self.stagedir])
        self.markDone('stage')
        return True

    def snap(self, force=False):
        if not self.shouldStageRun('snap', force):
            return True
        self.makedirs()

        if self.code and hasattr(self.code, 'snapFiles'):
            self.notifyStage("Snapping")

            includes, excludes = getattr(self.code, 'snapFiles')()
            snapDirs, snapFiles = self.collectSnapFiles(includes, excludes)

            if snapDirs:
                snapcraft.common.run(['mkdir', '-p'] + list(snapDirs), cwd=self.stagedir)
            if snapFiles:
                snapcraft.common.run(['cp', '-a', '--parent'] + list(snapFiles) + [self.snapdir], cwd=self.stagedir)

            self.markDone('snap')
        return True

    def collectSnapFiles(self, includes, excludes):
        sourceFiles = set()
        for root, dirs, files in os.walk(self.installdir):
            sourceFiles |= set([os.path.join(root, d) for d in dirs])
            sourceFiles |= set([os.path.join(root, f) for f in files])
        sourceFiles = set([os.path.relpath(x, self.installdir) for x in sourceFiles])

        includeFiles = set()
        for include in includes:
            matches = glob.glob(os.path.join(self.stagedir, include))
            includeFiles |= set(matches)
        includeDirs = [x for x in includeFiles if os.path.isdir(x)]
        includeFiles = set([os.path.relpath(x, self.stagedir) for x in includeFiles])

        # Expand includeFiles, so that an exclude like '*/*.so' will still match
        # files from an include like 'lib'
        for includeDir in includeDirs:
            for root, dirs, files in os.walk(includeDir):
                includeFiles |= set([os.path.relpath(os.path.join(root, d), self.stagedir) for d in dirs])
                includeFiles |= set([os.path.relpath(os.path.join(root, f), self.stagedir) for f in files])

        # Grab exclude list
        excludeFiles = set()
        for exclude in excludes:
            matches = glob.glob(os.path.join(self.stagedir, exclude))
            excludeFiles |= set(matches)
        excludeDirs = [os.path.relpath(x, self.stagedir) for x in excludeFiles if os.path.isdir(x)]
        excludeFiles = set([os.path.relpath(x, self.stagedir) for x in excludeFiles])

        # And chop files, including whole trees if any dirs are mentioned
        snapFiles = (includeFiles & sourceFiles) - excludeFiles
        for excludeDir in excludeDirs:
            snapFiles = set([x for x in snapFiles if not x.startswith(excludeDir + '/')])

        # Separate dirs from files
        snapDirs = set([x for x in snapFiles if os.path.isdir(os.path.join(self.stagedir, x))])
        snapFiles = snapFiles - snapDirs

        return snapDirs, snapFiles

    def env(self, root):
        if self.code and hasattr(self.code, 'env'):
            return getattr(self.code, 'env')(root)
        return []


def loadPlugin(partName, pluginName, properties={}, loadCode=True):
    part = Plugin(pluginName, partName, properties, loadCode=loadCode)
    if not part.isValid():
        snapcraft.common.log("Could not load part %s" % pluginName, file=sys.stderr)
        sys.exit(1)
    return part
