#!/usr/bin/env python2
# Author: Nic Wolfe <nic@wolfeden.ca>
# URL: http://code.google.com/p/sickbeard/
#
# This file is part of SickRage.
#
# SickRage is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# SickRage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with SickRage.  If not, see <http://www.gnu.org/licenses/>.

# Check needed software dependencies to nudge users to fix their setup
import sys
import tornado.ioloop

if sys.version_info < (2, 6):
    print "Sorry, requires Python 2.6 or 2.7."
    sys.exit(1)

try:
    import Cheetah

    if Cheetah.Version[0] != '2':
        raise ValueError
except ValueError:
    print "Sorry, requires Python module Cheetah 2.1.0 or newer."
    sys.exit(1)
except:
    print "The Python module Cheetah is required"
    sys.exit(1)

import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'lib')))

# We only need this for compiling an EXE and I will just always do that on 2.6+
if sys.hexversion >= 0x020600F0:
    from multiprocessing import freeze_support  # @UnresolvedImport

import locale
import datetime
import threading
import signal
import traceback
import getopt

import sickbeard

from sickbeard import db
from sickbeard.tv import TVShow
from sickbeard import logger
from sickbeard import webserveInit
from sickbeard.version import SICKBEARD_VERSION
from sickbeard.databases.mainDB import MIN_DB_VERSION
from sickbeard.databases.mainDB import MAX_DB_VERSION

from lib.configobj import ConfigObj

from tornado.ioloop import IOLoop, PeriodicCallback

signal.signal(signal.SIGINT, sickbeard.sig_handler)
signal.signal(signal.SIGTERM, sickbeard.sig_handler)

throwaway = datetime.datetime.strptime('20110101', '%Y%m%d')


def loadShowsFromDB():
    """
    Populates the showList with shows from the database
    """

    with db.DBConnection() as myDB:
        sqlResults = myDB.select("SELECT * FROM tv_shows")

        for sqlShow in sqlResults:
            try:
                curShow = TVShow(int(sqlShow["indexer"]), int(sqlShow["indexer_id"]))
                sickbeard.showList.append(curShow)
            except Exception, e:
                logger.log(
                    u"There was an error creating the show in " + sqlShow["location"] + ": " + str(e).decode('utf-8'),
                    logger.ERROR)
                logger.log(traceback.format_exc(), logger.DEBUG)

            # TODO: update the existing shows if the showlist has something in it


def daemonize():
    """
    Fork off as a daemon
    """

    # pylint: disable=E1101
    # Make a non-session-leader child process
    try:
        pid = os.fork()  # @UndefinedVariable - only available in UNIX
        if pid != 0:
            os._exit(0)
    except OSError, e:
        sys.stderr.write("fork #1 failed: %d (%s)\n" % (e.errno, e.strerror))
        sys.exit(1)

    os.setsid()  # unix

    # Make sure I can read my own files and shut out others
    prev = os.umask(0)
    os.umask(prev and int('077', 8))

    # Make the child a session-leader by detaching from the terminal
    try:
        pid = os.fork()  # @UndefinedVariable - only available in UNIX
        if pid != 0:
            os._exit(0)
    except OSError, e:
        sys.stderr.write("fork #2 failed: %d (%s)\n" % (e.errno, e.strerror))
        sys.exit(1)

    # Write pid
    if sickbeard.CREATEPID:
        pid = str(os.getpid())
        logger.log(u"Writing PID: " + pid + " to " + str(sickbeard.PIDFILE))
        try:
            file(sickbeard.PIDFILE, 'w').write("%s\n" % pid)
        except IOError, e:
            logger.log_error_and_exit(
                u"Unable to write PID file: " + sickbeard.PIDFILE + " Error: " + str(e.strerror) + " [" + str(
                    e.errno) + "]")

    # Redirect all output
    sys.stdout.flush()
    sys.stderr.flush()

    devnull = getattr(os, 'devnull', '/dev/null')
    stdin = file(devnull, 'r')
    stdout = file(devnull, 'a+')
    stderr = file(devnull, 'a+')
    os.dup2(stdin.fileno(), sys.stdin.fileno())
    os.dup2(stdout.fileno(), sys.stdout.fileno())
    os.dup2(stderr.fileno(), sys.stderr.fileno())

def main():
    """
    TV for me
    """

    # do some preliminary stuff
    sickbeard.MY_FULLNAME = os.path.normpath(os.path.abspath(__file__))
    sickbeard.MY_NAME = os.path.basename(sickbeard.MY_FULLNAME)
    sickbeard.PROG_DIR = os.path.dirname(sickbeard.MY_FULLNAME)
    sickbeard.DATA_DIR = sickbeard.PROG_DIR
    sickbeard.MY_ARGS = sys.argv[1:]
    sickbeard.DAEMON = False
    sickbeard.CREATEPID = False

    sickbeard.SYS_ENCODING = None

    try:
        locale.setlocale(locale.LC_ALL, "")
        sickbeard.SYS_ENCODING = locale.getpreferredencoding()
    except (locale.Error, IOError):
        pass

    # For OSes that are poorly configured I'll just randomly force UTF-8
    if not sickbeard.SYS_ENCODING or sickbeard.SYS_ENCODING in ('ANSI_X3.4-1968', 'US-ASCII', 'ASCII'):
        sickbeard.SYS_ENCODING = 'UTF-8'

    if not hasattr(sys, "setdefaultencoding"):
        reload(sys)

    try:
        # pylint: disable=E1101
        # On non-unicode builds this will raise an AttributeError, if encoding type is not valid it throws a LookupError
        sys.setdefaultencoding(sickbeard.SYS_ENCODING)
    except:
        print 'Sorry, you MUST add the SickRage folder to the PYTHONPATH environment variable'
        print 'or find another way to force Python to use ' + sickbeard.SYS_ENCODING + ' for string encoding.'
        sys.exit(1)

    # Need console logging for SickBeard.py and SickBeard-console.exe
    consoleLogging = (not hasattr(sys, "frozen")) or (sickbeard.MY_NAME.lower().find('-console') > 0)

    # Rename the main thread
    threading.currentThread().name = "MAIN"

    try:
        opts, args = getopt.getopt(sys.argv[1:], "qfdp::",
                                   ['quiet', 'forceupdate', 'daemon', 'port=', 'pidfile=', 'nolaunch', 'config=',
                                    'datadir='])  # @UnusedVariable
    except getopt.GetoptError:
        print "Available Options: --quiet, --forceupdate, --port, --daemon, --pidfile, --config, --datadir"
        sys.exit()

    forceUpdate = False
    forcedPort = None
    noLaunch = False

    for o, a in opts:
        # For now we'll just silence the logging
        if o in ('-q', '--quiet'):
            consoleLogging = False

        # Should we update (from indexer) all shows in the DB right away?
        if o in ('-f', '--forceupdate'):
            forceUpdate = True

        # Suppress launching web browser
        # Needed for OSes without default browser assigned
        # Prevent duplicate browser window when restarting in the app
        if o in ('--nolaunch',):
            noLaunch = True

        # Override default/configured port
        if o in ('-p', '--port'):
            forcedPort = int(a)

        # Run as a double forked daemon
        if o in ('-d', '--daemon'):
            sickbeard.DAEMON = True
            # When running as daemon disable consoleLogging and don't start browser
            consoleLogging = False
            noLaunch = True

            if sys.platform == 'win32':
                sickbeard.DAEMON = False

        # Specify folder to load the config file from
        if o in ('--config',):
            sickbeard.CONFIG_FILE = os.path.abspath(a)

        # Specify folder to use as the data dir
        if o in ('--datadir',):
            sickbeard.DATA_DIR = os.path.abspath(a)

        # Prevent resizing of the banner/posters even if PIL is installed
        if o in ('--noresize',):
            sickbeard.NO_RESIZE = True

        # Write a pidfile if requested
        if o in ('--pidfile',):
            sickbeard.CREATEPID = True
            sickbeard.PIDFILE = str(a)

            # If the pidfile already exists, sickbeard may still be running, so exit
            if os.path.exists(sickbeard.PIDFILE):
                sys.exit("PID file: " + sickbeard.PIDFILE + " already exists. Exiting.")

    # The pidfile is only useful in daemon mode, make sure we can write the file properly
    if sickbeard.CREATEPID:
        if sickbeard.DAEMON:
            pid_dir = os.path.dirname(sickbeard.PIDFILE)
            if not os.access(pid_dir, os.F_OK):
                sys.exit("PID dir: " + pid_dir + " doesn't exist. Exiting.")
            if not os.access(pid_dir, os.W_OK):
                sys.exit("PID dir: " + pid_dir + " must be writable (write permissions). Exiting.")

        else:
            if consoleLogging:
                sys.stdout.write("Not running in daemon mode. PID file creation disabled.\n")

            sickbeard.CREATEPID = False

    # If they don't specify a config file then put it in the data dir
    if not sickbeard.CONFIG_FILE:
        sickbeard.CONFIG_FILE = os.path.join(sickbeard.DATA_DIR, "config.ini")

    # Make sure that we can create the data dir
    if not os.access(sickbeard.DATA_DIR, os.F_OK):
        try:
            os.makedirs(sickbeard.DATA_DIR, 0744)
        except os.error, e:
            raise SystemExit("Unable to create datadir '" + sickbeard.DATA_DIR + "'")

    # Make sure we can write to the data dir
    if not os.access(sickbeard.DATA_DIR, os.W_OK):
        raise SystemExit("Datadir must be writeable '" + sickbeard.DATA_DIR + "'")

    # Make sure we can write to the config file
    if not os.access(sickbeard.CONFIG_FILE, os.W_OK):
        if os.path.isfile(sickbeard.CONFIG_FILE):
            raise SystemExit("Config file '" + sickbeard.CONFIG_FILE + "' must be writeable.")
        elif not os.access(os.path.dirname(sickbeard.CONFIG_FILE), os.W_OK):
            raise SystemExit("Config file root dir '" + os.path.dirname(sickbeard.CONFIG_FILE) + "' must be writeable.")

    os.chdir(sickbeard.DATA_DIR)

    if consoleLogging:
        print "Starting up SickRage " + SICKBEARD_VERSION + " from " + sickbeard.CONFIG_FILE

    # Load the config and publish it to the sickbeard package
    if not os.path.isfile(sickbeard.CONFIG_FILE):
        logger.log(u"Unable to find '" + sickbeard.CONFIG_FILE + "' , all settings will be default!", logger.ERROR)

    sickbeard.CFG = ConfigObj(sickbeard.CONFIG_FILE)

    with db.DBConnection() as myDB:
        CUR_DB_VERSION = myDB.checkDBVersion()

    if CUR_DB_VERSION > 0:
        if CUR_DB_VERSION < MIN_DB_VERSION:
            raise SystemExit("Your database version (" + str(
                CUR_DB_VERSION) + ") is too old to migrate from with this version of SickRage (" + str(
                MIN_DB_VERSION) + ").\n" + \
                             "Upgrade using a previous version of SB first, or start with no database file to begin fresh.")
        if CUR_DB_VERSION > MAX_DB_VERSION:
            raise SystemExit("Your database version (" + str(
                CUR_DB_VERSION) + ") has been incremented past what this version of SickRage supports (" + str(
                MAX_DB_VERSION) + ").\n" + \
                             "If you have used other forks of SB, your database may be unusable due to their modifications.")

    # Initialize the config and our threads
    sickbeard.initialize(consoleLogging=consoleLogging)

    sickbeard.showList = []

    if sickbeard.DAEMON:
        daemonize()

    # Use this PID for everything
    sickbeard.PID = os.getpid()

    if forcedPort:
        logger.log(u"Forcing web server to port " + str(forcedPort))
        startPort = forcedPort
    else:
        startPort = sickbeard.WEB_PORT

    if sickbeard.WEB_LOG:
        log_dir = sickbeard.LOG_DIR
    else:
        log_dir = None

    # sickbeard.WEB_HOST is available as a configuration value in various
    # places but is not configurable. It is supported here for historic reasons.
    if sickbeard.WEB_HOST and sickbeard.WEB_HOST != '0.0.0.0':
        webhost = sickbeard.WEB_HOST
    else:
        if sickbeard.WEB_IPV6:
            webhost = '::'
        else:
            webhost = '0.0.0.0'

    options = {
        'port': int(startPort),
        'host': webhost,
        'data_root': os.path.join(sickbeard.PROG_DIR, 'gui', sickbeard.GUI_NAME),
        'web_root': sickbeard.WEB_ROOT,
        'log_dir': log_dir,
        'username': sickbeard.WEB_USERNAME,
        'password': sickbeard.WEB_PASSWORD,
        'enable_https': sickbeard.ENABLE_HTTPS,
        'handle_reverse_proxy': sickbeard.HANDLE_REVERSE_PROXY,
        'https_cert': sickbeard.HTTPS_CERT,
        'https_key': sickbeard.HTTPS_KEY,
    }

    # init tornado
    webserveInit.initWebServer(options)

    # Build from the DB to start with
    logger.log(u"Loading initial show list")
    loadShowsFromDB()

    def startup():
        # Fire up all our threads
        sickbeard.start()

        # Launch browser if we're supposed to
        if sickbeard.LAUNCH_BROWSER and not noLaunch and not sickbeard.DAEMON:
            sickbeard.launchBrowser(startPort)

        # Start an update if we're supposed to
        if forceUpdate or sickbeard.UPDATE_SHOWS_ON_START:
            sickbeard.showUpdateScheduler.action.run(force=True)  # @UndefinedVariable

    # init startup tasks
    IOLoop.current().add_timeout(datetime.timedelta(seconds=5), startup)

    # start IOLoop
    IOLoop.current().start()
    sickbeard.saveAndShutdown()
    return

if __name__ == "__main__":
    if sys.hexversion >= 0x020600F0:
        freeze_support()
    main()
