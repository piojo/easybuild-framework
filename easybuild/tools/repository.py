##
# Copyright 2009-2013 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://vscentrum.be/nl/en),
# the Hercules foundation (http://www.herculesstichting.be/in_English)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# http://github.com/hpcugent/easybuild
#
# EasyBuild is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# EasyBuild is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EasyBuild.  If not, see <http://www.gnu.org/licenses/>.
##
"""
Set of repository tools

We have a plain filesystem, an svn and a git repository

@author: Stijn De Weirdt (Ghent University)
@author: Dries Verdegem (Ghent University)
@author: Kenneth Hoste (Ghent University)
@author: Pieter De Baets (Ghent University)
@author: Jens Timmerman (Ghent University)
@author: Toon Willems (Ghent University)
"""
import getpass
import os
import shutil
import socket
import tempfile
import time

from easybuild.tools.filetools import rmtree2

# optional Python packages, these might be missing
# failing imports are just ignored
# a NameError should be catched where these are used

# GitPython
try:
    import git
    from git import GitCommandError
except ImportError:
    pass

# PySVN
try:
    import pysvn  #@UnusedImport
    from pysvn import ClientError #IGNORE:E0611 pysvn fails to recognize ClientError is available
except ImportError:
    pass

from easybuild.framework.easyconfig import EasyConfig, stats_to_str
from easybuild.tools.build_log import get_log
from easybuild.tools.version import VERBOSE_VERSION


log = get_log('repo')

class Repository(object):
    """
    Interface for repositories
    """
    def __init__(self, repo_path, subdir=''):
        """
        Initialize a repository. self.repo and self.subdir will be set.
        self.wc will be set to None.
        Then, setupRepo and createWorkingCopy will be called (in that order)
        """
        self.subdir = subdir
        self.repo = repo_path
        self.wc = None
        self.setup_repo()
        self.create_working_copy()

    def setup_repo(self):
        """
        Set up repository.
        """
        pass

    def create_working_copy(self):
        """
        Create working copy.
        """
        pass

    def add_easyconfig(self, cfg, name, version, stats, previous):
        """
        Add easyconfig to repository.
        cfg is the filename of the eb file
        Stats contains some build stats, this should be a list of dictionaries.
        previous is the list of previous buildstats
        """
        pass

    def commit(self, msg=None):
        """
        Commit working copy
        - add msg
        - add more info to msg
        """
        # does nothing by default
        pass

    def cleanup(self):
        """
        Clean up working copy.
        """
        pass

    def get_buildstats(self, name, version):
        """
        Get the build statististics for module with name and version
        """
        pass


class FileRepository(Repository):
    """Class for file repositories."""

    def setup_repo(self):
        """
        for file based repos this will create the repo directory
        if it doesn't exist.

        if a subdir is specified also create the subdir
        """
        if not os.path.isdir(self.repo):
            os.makedirs(self.repo)

        full_path = os.path.join(self.repo, self.subdir)
        if not os.path.isdir(full_path):
            os.makedirs(full_path)

    def create_working_copy(self):
        """ set the working directory to the repo directory """
        # for sake of convenience
        self.wc = self.repo

    def add_easyconfig(self, cfg, name, version, stats, previous):
        """
        Add the eb-file for for software name and version to the repository.
        stats should be a dict containing stats.
        if previous is true -> append the stats to the file
        This will return the path to the created file (for use in subclasses)
        """
        # create directory for eb file
        full_path = os.path.join(self.wc, self.subdir, name)
        if not os.path.isdir(full_path):
            os.makedirs(full_path)

        ## destination
        dest = os.path.join(full_path, "%s.eb" % version)

        try:
            dest_file = open(dest, 'w')
            dest_file.write("# Built with %s on %s\n" % (VERBOSE_VERSION, time.strftime("%Y-%m-%d_%H-%M-%S")))

            # copy file
            for line in open(cfg):
                dest_file.write(line)

            # append a line to the eb file so we don't have git merge conflicts
            if not previous:
                statsprefix = "\n# Build statistics\nbuildstats = ["
                statssuffix = "]\n"
            else:
                #statstemplate = "\nbuildstats.append(%s)\n"
                statsprefix = "\nbuildstats.append("
                statssuffix = ")\n"

            dest_file.write(statsprefix + stats_to_str(stats) + statssuffix)
            dest_file.close()

        except IOError, err:
            log.exception("Copying file %s to %s (wc: %s) failed (%s)" % (cfg, dest, self.wc, err))

        return dest

    def get_buildstats(self, name, version):
        """
        return the build statistics
        """
        full_path = os.path.join(self.wc, self.subdir, name)
        if not os.path.isdir(full_path):
            log.debug("module (%s) has not been found in the repo" % name)
            return []

        dest = os.path.join(full_path, "%s.eb" % version)
        if not os.path.isfile(dest):
            log.debug("version (%s) of module (%s) has not been found in the repo" % (version, name))
            return []

        eb = EasyConfig(dest, validate=False)
        return eb['buildstats']


class GitRepository(FileRepository):
    """
    Class for git repositories.
    """

    def __init__(self, *args):
        """
        Initialize git client to None (will be set later)
        All the real logic is in the setupRepo and createWorkingCopy methods
        """
        self.client = None
        FileRepository.__init__(self, *args)

    def setup_repo(self):
        """
        Set up git repository.
        """
        try:
            git.GitCommandError
        except NameError, err:
            log.exception("It seems like GitPython is not available: %s" % err)
        self.wc = tempfile.mkdtemp(prefix='git-wc-')

    def create_working_copy(self):
        """
        Create git working copy.
        """

        reponame = 'UNKNOWN'
        ## try to get a copy of
        try:
            client = git.Git(self.wc)
            out = client.clone(self.repo)
            # out  = 'Cloning into easybuild...'
            reponame = out.split("\n")[0].split()[-1].strip(".").strip("'")
            log.debug("rep name is %s" % reponame)
        except git.GitCommandError, err:
            # it might already have existed
            log.warning("Git local repo initialization failed, it might already exist: %s" % err)

        # local repo should now exist, let's connect to it again
        try:
            self.wc = os.path.join(self.wc, reponame)
            log.debug("connectiong to git repo in %s" % self.wc)
            self.client = git.Git(self.wc)
        except (git.GitCommandError, OSError), err:
            log.error("Could not create a local git repo in wc %s: %s" % (self.wc, err))

        # try to get the remote data in the local repo
        try:
            res = self.client.pull()
            log.debug("pulled succesfully to %s in %s" % (res, self.wc))
        except (git.GitCommandError, OSError), err:
            log.exception("pull in working copy %s went wrong: %s" % (self.wc, err))

    def add_easyconfig(self, cfg, name, version, stats, append):
        """
        Add easyconfig to git repository.
        """
        dest = FileRepository.add_easyconfig(self, cfg, name, version, stats, append)
        ## add it to version control
        if dest:
            try:
                self.client.add(dest)
            except GitCommandError, err:
                log.warning("adding %s to git failed: %s" % (dest, err))

    def commit(self, msg=None):
        """
        Commit working copy to git repository
        """
        log.debug("committing in git: %s" % msg)
        completemsg = "EasyBuild-commit from %s (time: %s, user: %s) \n%s" % (socket.gethostname(), time.strftime("%Y-%m-%d_%H-%M-%S"), getpass.getuser(), msg)
        log.debug("git status: %s" % self.client.status())
        try:
            self.client.commit('-am "%s"' % completemsg)
            log.debug("succesfull commit")
        except GitCommandError, err:
            log.warning("Commit from working copy %s (msg: %s) failed, empty commit?\n%s" % (self.wc, msg, err))
        try:
            info = self.client.push()
            log.debug("push info: %s " % info)
        except GitCommandError, err:
            log.warning("Push from working copy %s to remote %s (msg: %s) failed: %s" % (self.wc, self.repo, msg, err))

    def cleanup(self):
        """
        Clean up git working copy.
        """
        try:
            rmtree2(self.wc)
        except IOError, err:
            log.exception("Can't remove working copy %s: %s" % (self.wc, err))


class SvnRepository(FileRepository):
    """
    Class for svn repositories
    """

    def __init__(self, *args):
        """
        Set self.client to None. Real logic is in setupRepo and createWorkingCopy
        """
        self.client = None
        FileRepository.__init__(self, *args)

    def setup_repo(self):
        """
        Set up SVN repository.
        """
        self.repo = os.path.join(self.repo, self.subdir)
        try:
            raise pysvn.ClientError #IGNORE:E0611 pysvn fails to recognize ClientError is available
        except NameError, err:
            log.exception("pysvn not available (%s). Make sure it is installed " % err +
                          "properly. Run 'python -c \"import pysvn\"' to test.")

        ## try to connect to the repository
        log.debug("Try to connect to repository %s" % self.repo)
        try:
            self.client = pysvn.Client()
            self.client.exception_style = 0
        except ClientError:
            log.exception("Svn Client initialization failed.")

        try:
            if not self.client.is_url(self.repo):
                log.error("Provided repository %s is not a valid svn url" % self.repo)
        except ClientError:
            log.exception("Can't connect to svn repository %s" % self.repo)

    def create_working_copy(self):
        """
        Create SVN working copy.
        """
        self.wc = tempfile.mkdtemp(prefix='svn-wc-')

        ## check if tmppath exists
        ## this will trigger an error if it does not exist
        try:
            self.client.info2(self.repo, recurse=False)
        except ClientError:
            log.exception("Getting info from %s failed." % self.wc)

        try:
            res = self.client.update(self.wc)
            log.debug("Updated to revision %s in %s" % (res, self.wc))
        except ClientError:
            log.exception("Update in wc %s went wrong" % self.wc)

        if len(res) == 0:
            log.error("Update returned empy list (working copy: %s)" % (self.wc))

        if res[0].number == -1:
            ## revision number of update is -1
            ## means nothing has been checked out
            try:
                res = self.client.checkout(self.repo, self.wc)
                log.debug("Checked out revision %s in %s" % (res.number, self.wc))
            except ClientError, err:
                log.exception("Checkout of path / in working copy %s went wrong: %s" % (self.wc, err))

    def add_easyconfig(self, cfg, name, version, stats, append):
        """
        Add easyconfig to SVN repository.
        """
        dest = FileRepository.add_easyconfig(self, cfg, name, version, stats, append)
        log.debug("destination = %s" % dest)
        if dest:
            log.debug("destination status: %s" % self.client.status(dest))

            if self.client and not self.client.status(dest)[0].is_versioned:
                ## add it to version control
                log.debug("Going to add %s (working copy: %s, cwd %s)" % (dest, self.wc, os.getcwd()))
                self.client.add(dest)


    def commit(self, msg=None):
        """
        Commit working copy to SVN repository
        """
        completemsg = "EasyBuild-commit from %s (time: %s, user: %s) \n%s" % (socket.gethostname(), time.strftime("%Y-%m-%d_%H-%M-%S"), getpass.getuser(), msg)
        try:
            self.client.checkin(self.wc, completemsg, recurse=True)
        except ClientError, err:
            log.exception("Commit from working copy %s (msg: %s) failed: %s" % (self.wc, msg, err))

    def cleanup(self):
        """
        Clean up SVN working copy.
        """
        try:
            rmtree2(self.wc)
        except OSError, err:
            log.exception("Can't remove working copy %s: %s" % (self.wc, err))

