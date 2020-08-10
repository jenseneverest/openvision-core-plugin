#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import print_function
# for localized messages
from boxbranding import getVisionVersion, getImageDistro, getImageVersion, getVisionRevision, getImageDevBuild, getKernelVersion
from os import path, stat, mkdir, listdir, remove, statvfs, chmod
from time import localtime, time, strftime, mktime
from datetime import date, datetime
import tarfile, glob
from enigma import eTimer, eEnv, eDVBDB, quitMainloop, getBoxType
from . import _, PluginLanguageDomain
from Components.About import about
from Components.ActionMap import ActionMap
from Components.Button import Button
from Components.config import configfile, config, ConfigSubsection, ConfigYesNo, ConfigSelection, ConfigText, ConfigNumber, ConfigLocations, NoSave, ConfigClock, ConfigDirectory, getConfigListEntry
from Components.ConfigList import ConfigListScreen
from Components.Console import Console
from Components.FileList import MultiFileSelectList, FileList
from Components.Harddisk import harddiskmanager
from Components.Label import Label
from Components.MenuList import MenuList
from Components.ScrollLabel import ScrollLabel
from Components.Sources.StaticText import StaticText
from Components.SystemInfo import SystemInfo
import Components.Task
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Screens.Setup import Setup
from Tools.Notifications import AddPopupWithCallback

currentkernelversion = getKernelVersion()

autoBackupManagerTimer = None
SETTINGSRESTOREQUESTIONID = 'RestoreSettingsNotification'
PLUGINRESTOREQUESTIONID = 'RestorePluginsNotification'
NOPLUGINS = 'NoPluginsNotification'

hddchoices = []
for p in harddiskmanager.getMountedPartitions():
	if path.exists(p.mountpoint):
		d = path.normpath(p.mountpoint)
		if SystemInfo["canMultiBoot"]:
			if "mmcblk0p" in d or "mmcblk1p" in d:
				continue
		if p.mountpoint != '/':
			hddchoices.append((p.mountpoint, d))

config.backupmanager = ConfigSubsection()
defaultprefix = getImageDistro()[4:]
config.backupmanager.folderprefix = ConfigText(default=defaultprefix, fixed_size=False)
config.backupmanager.backuplocation = ConfigSelection(choices=hddchoices)
config.backupmanager.schedule = ConfigYesNo(default=False)
config.backupmanager.scheduletime = ConfigClock(default=0)  # 1:00
config.backupmanager.repeattype = ConfigSelection(default="daily", choices=[("daily", _("Daily")), ("weekly", _("Weekly")), ("monthly", _("30 Days"))])

# Querying is enabled by default - asthat is what used to happen always
#
config.backupmanager.query = ConfigYesNo(default=True)

# If we do not yet have a record of a backup, assume it has never happened.
#
config.backupmanager.lastbackup = ConfigNumber(default=0)

# Max no. of backups to keep.  0 == keep them all
#
config.backupmanager.number_to_keep = ConfigNumber(default=0)

config.backupmanager.backupretry = ConfigNumber(default=30)
config.backupmanager.backupretrycount = NoSave(ConfigNumber(default=0))
config.backupmanager.nextscheduletime = NoSave(ConfigNumber(default=0))
config.backupmanager.backupdirs = ConfigLocations(
	default=[eEnv.resolve('${sysconfdir}/enigma2/'), eEnv.resolve('${sysconfdir}/fstab'), eEnv.resolve('${sysconfdir}/hostname'), eEnv.resolve('${sysconfdir}/network/interfaces'), eEnv.resolve('${sysconfdir}/passwd'), eEnv.resolve('${sysconfdir}/shadow'), eEnv.resolve('${sysconfdir}/etc/shadow'),
			 eEnv.resolve('${sysconfdir}/resolv.conf'), eEnv.resolve('${sysconfdir}/ushare.conf'), eEnv.resolve('${sysconfdir}/inadyn.conf'), eEnv.resolve('${sysconfdir}/tuxbox/config/'), eEnv.resolve('${sysconfdir}/wpa_supplicant.conf'), '/usr/softcams/'])
config.backupmanager.xtraplugindir = ConfigDirectory(default='')
config.backupmanager.lastlog = ConfigText(default=' ', fixed_size=False)

def isRestorableSettings(imageversion):
	minimum_version = 4.2
	try:
		imageversion = float(imageversion)
	except:
		return False
	return imageversion >= minimum_version

def isRestorablePlugins(imageversion):
	minimum_version = 4.2
	try:
		imageversion = float(imageversion)
	except:
		return False
	return imageversion >= minimum_version

def BackupManagerautostart(reason, session=None, **kwargs):
	"""called with reason=1 to during /sbin/shutdown.sysvinit, with reason=0 at startup?"""
	global autoBackupManagerTimer
	global _session
	now = int(time())
	if reason == 0:
		print("[BackupManager] AutoStart Enabled")
		if session is not None:
			_session = session
			if autoBackupManagerTimer is None:
				autoBackupManagerTimer = AutoBackupManagerTimer(session)
	else:
		if autoBackupManagerTimer is not None:
			print("[BackupManager] Stop")
			autoBackupManagerTimer.stop()


class VISIONBackupManager(Screen):
	skin = """<screen name="VISIONBackupManager" position="center,center" size="560,400">
		<ePixmap pixmap="buttons/red.png" position="0,0" size="140,40" alphatest="on" />
		<ePixmap pixmap="buttons/green.png" position="140,0" size="140,40" alphatest="on" />
		<ePixmap pixmap="buttons/yellow.png" position="280,0" size="140,40" alphatest="on" />
		<widget name="key_red" position="0,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#9f1313" transparent="1" />
		<widget name="key_green" position="140,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#1f771f" transparent="1" />
		<widget name="key_yellow" position="280,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#a08500" transparent="1" />
		<ePixmap pixmap="buttons/key_menu.png" position="0,40" size="35,25" alphatest="blend" transparent="1" zPosition="3" />
		<ePixmap pixmap="buttons/key_info.png" position="40,40" size="35,25" alphatest="blend" transparent="1" zPosition="3" />
		<widget name="lab1" position="0,50" size="560,50" font="Regular; 18" zPosition="2" transparent="0" halign="center" />
		<widget name="list" position="10,105" size="540,260" scrollbarMode="showOnDemand" />
		<widget name="backupstatus" position="10,370" size="400,30" font="Regular;20" zPosition="5" />
		<applet type="onLayoutFinish">
		self["list"].instance.setItemHeight(25)
		</applet>
	</screen>"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self.setTitle(_("Vision Backup manager"))

		self['lab1'] = Label()
		self["backupstatus"] = Label()
		self["key_green"] = Button()
		self["key_yellow"] = Button(_("Restore"))
		self["key_red"] = Button(_("Delete"))

		self.BackupRunning = False
		self.BackupDirectory = " "
		self.onChangedEntry = []
		self.emlist = []
		self['list'] = MenuList(self.emlist)
		self.populate_List()
		self.activityTimer = eTimer()
		self.activityTimer.timeout.get().append(self.backupRunning)
		self.activityTimer.start(10)
		self.Console = Console()

		if BackupTime > 0:
			t = localtime(BackupTime)
			backuptext = _("Next backup: ") + strftime(_("%a %e %b  %-H:%M"), t)
		else:
			backuptext = _("Next backup: ")
		self["backupstatus"].setText(str(backuptext))
		if not self.selectionChanged in self["list"].onSelectionChanged:
			self["list"].onSelectionChanged.append(self.selectionChanged)

	def createSummary(self):
		from Screens.PluginBrowser import PluginBrowserSummary

		return PluginBrowserSummary

	def selectionChanged(self):
		item = self["list"].getCurrent()
		desc = self["backupstatus"].text
		if item:
			name = item
		else:
			name = ""
		for cb in self.onChangedEntry:
			cb(name, desc)

	def backupRunning(self):
		self.populate_List()
		self.BackupRunning = False
		for job in Components.Task.job_manager.getPendingJobs():
			if job.name.startswith(_("Backup manager")):
				self.BackupRunning = True
		if self.BackupRunning:
			self["key_green"].setText(_("View progress"))
		else:
			self["key_green"].setText(_("New backup"))
		self.activityTimer.startLongTimer(5)

	def getJobName(self, job):
		return "%s: %s (%d%%)" % (job.getStatustext(), job.name, int(100 * job.progress / float(job.end)))

	def showJobView(self, job):
		from Screens.TaskView import JobView
		Components.Task.job_manager.in_background = False
		self.session.openWithCallback(self.JobViewCB, JobView, job, cancelable=False, afterEventChangeable=False)

	def JobViewCB(self, in_background):
		Components.Task.job_manager.in_background = in_background

	def populate_List(self):
		if config.backupmanager.backuplocation.getValue():
			mount = config.backupmanager.backuplocation.value, config.backupmanager.backuplocation.value[:-1]
		else:
			mount = config.backupmanager.backuplocation.value + "/", config.backupmanager.backuplocation.value
		hdd = "/media/hdd/", "/media/hdd"
		if mount in config.backupmanager.backuplocation.choices.choices and hdd not in config.backupmanager.backuplocation.choices.choices:
			self["myactions"] = ActionMap(["OkCancelActions", "MenuActions"], {
				"cancel": self.close,
				"menu": self.createSetup,
			}, -1)
			self["lab1"].setText(_("Device: None available") + "\n" + _("Press 'Menu' to select a storage device"))
		else:
			self['myactions'] = ActionMap(['ColorActions', 'OkCancelActions', 'DirectionActions', "MenuActions", "TimerEditActions"],
										  {
										  'cancel': self.close,
										  'ok': self.keyResstore,
										  'red': self.keyDelete,
										  'green': self.GreenPressed,
										  'yellow': self.keyResstore,
										  "menu": self.createSetup,
										  'log': self.showLog,
										  }, -1)
			if mount not in config.backupmanager.backuplocation.choices.choices:
					self.BackupDirectory = '/media/hdd/backup/'
					config.backupmanager.backuplocation.value = '/media/hdd/'
					config.backupmanager.backuplocation.save()
					self['lab1'].setText(_("The chosen location does not exist, using /media/hdd.") + "\n" + _("Select a backup to restore:"))
			else:
				self.BackupDirectory = config.backupmanager.backuplocation.value + 'backup/'
				self['lab1'].setText(_("Device: ") + config.backupmanager.backuplocation.value + "\n" + _("Select a backup to restore:"))
			if mount not in config.backupmanager.backuplocation.choices.choices and hdd not in config.backupmanager.backuplocation.choices.choices:
					self.BackupDirectory = '/media/usb/backup/'
					config.backupmanager.backuplocation.value = '/media/usb/'
					config.backupmanager.backuplocation.save()
					self['lab1'].setText(_("The chosen location does not exist, using /media/usb.") + "\n" + _("Select a backup to restore:"))
			else:
				self.BackupDirectory = config.backupmanager.backuplocation.value + 'backup/'
				self['lab1'].setText(_("Device: ") + config.backupmanager.backuplocation.value + "\n" + _("Select a backup to restore:"))
			try:
				if not path.exists(self.BackupDirectory):
					mkdir(self.BackupDirectory, 0755)
				images = listdir(self.BackupDirectory)
				del self.emlist[:]
				mtimes = []
				for fil in images:
					if fil.endswith('.tar.gz'): # prefix should only be used for naming files, not browsing them...
						if fil.startswith(defaultprefix):   # Ensure the current image backup are sorted to the top
							prefix="B"
						else:
							prefix="A"
						key = "%s-%012u" % (prefix, stat(self.BackupDirectory + fil).st_mtime)
						mtimes.append((fil, key)) # (filname, prefix-mtime)
				for fil in [x[0] for x in sorted(mtimes, key=lambda x: x[1], reverse=True)]: # sort by mtime
					self.emlist.append(fil)
				self["list"].setList(self.emlist)
				self["list"].show()
			except:
				self['lab1'].setText(_("Device: ") + config.backupmanager.backuplocation.value + "\n" + _("There is a problem with this device. Please reformat it and try again."))

	def createSetup(self):
		self.session.openWithCallback(self.setupDone, VISIONBackupManagerMenu)

	def showLog(self):
		self.sel = self['list'].getCurrent()
		if self.sel:
			filename = self.BackupDirectory + self.sel
			self.session.open(VISIONBackupManagerLogView, filename)

	def setupDone(self, test=None):
		if config.backupmanager.folderprefix.value == '':
			config.backupmanager.folderprefix.value = defaultprefix
			config.backupmanager.folderprefix.save()
# If the prefix doesn't start with the defaultprefix it is a tag...
#
		if not config.backupmanager.folderprefix.value.startswith(defaultprefix):
			config.backupmanager.folderprefix.value = defaultprefix + "-" + config.backupmanager.folderprefix.value
			config.backupmanager.folderprefix.save()
		self.populate_List()
		self.doneConfiguring()

	def doneConfiguring(self):
		now = int(time())
		if config.backupmanager.schedule.value:
			if autoBackupManagerTimer is not None:
				print("[BackupManager] Backup schedule enabled at", strftime("%c", localtime(now)))
				autoBackupManagerTimer.backupupdate()
		else:
			if autoBackupManagerTimer is not None:
				global BackupTime
				BackupTime = 0
				print("[BackupManager] Backup schedule disabled at", strftime("%c", localtime(now)))
				autoBackupManagerTimer.backupstop()
		if BackupTime > 0:
			t = localtime(BackupTime)
			backuptext = _("Next backup: ") + strftime(_("%a %e %b  %-H:%M"), t)
		else:
			backuptext = _("Next backup: ")
		self["backupstatus"].setText(str(backuptext))

	def keyDelete(self):
		self.sel = self['list'].getCurrent()
		if self.sel:
			message = _("Are you sure you want to delete this backup:\n ") + self.sel
			ybox = self.session.openWithCallback(self.doDelete, MessageBox, message, MessageBox.TYPE_YESNO, default=False)
			ybox.setTitle(_("Remove confirmation"))
		else:
			self.session.open(MessageBox, _("There is no backup to delete."), MessageBox.TYPE_INFO, timeout=10)

	def doDelete(self, answer):
		if answer is True:
			self.sel = self['list'].getCurrent()
			self["list"].instance.moveSelectionTo(0)
			remove(self.BackupDirectory + self.sel)
		self.populate_List()

	def GreenPressed(self):
		self.BackupRunning = False
		for job in Components.Task.job_manager.getPendingJobs():
			if job.name.startswith(_("Backup manager")):
				self.BackupRunning = True
				break
		if self.BackupRunning:
			self.showJobView(job)
		else:
			self.keyBackup()

	def keyBackup(self):
		self.BackupFiles = BackupFiles(self.session)
		Components.Task.job_manager.AddJob(self.BackupFiles.createBackupJob())
		self.BackupRunning = True
		self["key_green"].setText(_("View progress"))
		self["key_green"].show()
		for job in Components.Task.job_manager.getPendingJobs():
			if job.name.startswith(_("Backup manager")):
				self.showJobView(job)
				break

	def keyResstore(self):
		self.sel = self['list'].getCurrent()
		if not self.BackupRunning:
			if self.sel:
				if path.exists('/tmp/ExtraInstalledPlugins'):
					remove('/tmp/ExtraInstalledPlugins')
				if path.exists('/tmp/backupkernelversion'):
					remove('/tmp/backupkernelversion')
				self.Console.ePopen("tar -xzvf " + self.BackupDirectory + self.sel + " tmp/ExtraInstalledPlugins tmp/backupkernelversion tmp/backupimageversion -C /", self.settingsRestoreCheck)
			else:
				self.session.open(MessageBox, _("There is no backup to restore."), MessageBox.TYPE_INFO, timeout=10)
		else:
			self.session.open(MessageBox, _("Backup in progress,\nPlease wait for it to finish, before trying again."), MessageBox.TYPE_INFO, timeout=10)

	def settingsRestoreCheck(self, result, retval, extra_args=None):
		if path.exists('/tmp/backupkernelversion'):
			kernel = open('/tmp/backupkernelversion').read()
			print('[BackupManager] Backup Image:', kernel)
			print('[BackupManager] Current Image:', currentkernelversion)
			if kernel == currentkernelversion:
				print('[BackupManager] Stage 1: Image ver OK')
				self.keyResstore1()
			else:
				self.session.open(MessageBox, _("Sorry, but the file is not compatible with this kernel version."), MessageBox.TYPE_INFO, timeout=10)
		else:
			self.session.open(MessageBox, _("Sorry, but the file is not compatible with this kernel version."), MessageBox.TYPE_INFO, timeout=10)

	def keyResstore1(self):
		message = _("Are you sure you want to restore this backup:\n ") + self.sel
		ybox = self.session.openWithCallback(self.doRestore, MessageBox, message, MessageBox.TYPE_YESNO)
		ybox.setTitle(_("Restore confirmation"))

	def doRestore(self, answer):
		if answer is True:
			Components.Task.job_manager.AddJob(self.createRestoreJob())
			self.BackupRunning = True
			self["key_green"].setText(_("View progress"))
			self["key_green"].show()
			for job in Components.Task.job_manager.getPendingJobs():
				if job.name.startswith(_("Backup manager")):
					self.showJobView(job)
					break

	def myclose(self):
		self.close()

	def createRestoreJob(self):
		self.pluginslist = ""
		self.pluginslist2 = ""
		self.didSettingsRestore = False
		self.doPluginsRestore = False
		self.didPluginsRestore = False
		self.Stage1Completed = False
		self.Stage2Completed = False
		self.Stage3Completed = False
		self.Stage4Completed = False
		self.Stage5Completed = False
		job = Components.Task.Job(_("Backup manager"))

		task = Components.Task.PythonTask(job, _("Restoring backup..."))
		task.work = self.JobStart
		task.weighting = 1

		task = Components.Task.PythonTask(job, _("Restoring backup..."))
		task.work = self.Stage1
		task.weighting = 1

		task = Components.Task.ConditionTask(job, _("Restoring backup..."), timeoutCount=30)
		task.check = lambda: self.Stage1Completed
		task.weighting = 1

		task = Components.Task.PythonTask(job, _("Creating list of installed plugins..."))
		task.work = self.Stage2
		task.weighting = 1

		task = Components.Task.ConditionTask(job, _("Creating list of installed plugins..."), timeoutCount=300)
		task.check = lambda: self.Stage2Completed
		task.weighting = 1

		task = Components.Task.PythonTask(job, _("Comparing against backup..."))
		task.work = self.Stage3
		task.weighting = 1

		task = Components.Task.ConditionTask(job, _("Comparing against backup..."), timeoutCount=300)
		task.check = lambda: self.Stage3Completed
		task.weighting = 1

		task = Components.Task.PythonTask(job, _("Restoring plugins..."))
		task.work = self.Stage4
		task.weighting = 1

		task = Components.Task.ConditionTask(job, _("Restoring plugins..."), timeoutCount=300)
		task.check = lambda: self.Stage4Completed
		task.weighting = 1

		task = Components.Task.PythonTask(job, _("Restoring plugins, this can take a long time..."))
		task.work = self.Stage5
		task.weighting = 1

		task = Components.Task.ConditionTask(job, _("Restoring plugins, this can take a long time..."), timeoutCount=1200)
		task.check = lambda: self.Stage5Completed
		task.weighting = 1

		task = Components.Task.PythonTask(job, _("Rebooting..."))
		task.work = self.Stage6
		task.weighting = 1

		return job

	def JobStart(self):
		AddPopupWithCallback(self.Stage1,
							 _("Do you want to restore your enigma2 settings ?"),
							 MessageBox.TYPE_YESNO,
							 10,
							 SETTINGSRESTOREQUESTIONID
		)

	def Stage1(self, answer=None):
		print('[BackupManager] Restoring Stage 1:')
		if answer is True:
			self.Console.ePopen("tar -xzvf " + self.BackupDirectory + self.sel + " -C /", self.Stage1SettingsComplete)
		elif answer is False:
			self.Console.ePopen("tar -xzvf " + self.BackupDirectory + self.sel + " tmp/ExtraInstalledPlugins tmp/backupkernelversion tmp/backupimageversion  tmp/3rdPartyPlugins -C /", self.Stage1PluginsComplete)

	def Stage1SettingsComplete(self, result, retval, extra_args):
		print('[BackupManager] Restoring Stage 1 RESULT:', result)
		print('[BackupManager] Restoring Stage 1 retval:', retval)
		if retval == 0:
			print('[BackupManager] Restoring Stage 1 Complete:')
			self.didSettingsRestore = True
			self.Stage1Completed = True
			eDVBDB.getInstance().reloadServicelist()
			eDVBDB.getInstance().reloadBouquets()
			self.session.nav.PowerTimer.loadTimer()
# Don't check RecordTimers for conflicts. On a restore we may
# not have the correct tuner configuration (and no USB tuners)...
#
			self.session.nav.RecordTimer.loadTimer(justLoad=True)
			configfile.load()
		else:
			print('[BackupManager] Restoring Stage 1 Failed:')
			AddPopupWithCallback(self.Stage2,
								 _("Sorry, but the restore failed."),
								 MessageBox.TYPE_INFO,
								 10,
								 'StageOneFailedNotification'
			)

	def Stage1PluginsComplete(self, result, retval, extra_args):
		print('[BackupManager] Restoring Stage 1 Complete:')
		self.Stage1Completed = True

	def Stage2(self, result=False):
		print('[BackupManager] Restoring Stage 2: Checking feeds')
		self.Console.ePopen('opkg update', self.Stage2Complete)

	def Stage2Complete(self, result, retval, extra_args):
		print('[BackupManager] Restoring Stage 2: Result ', result)
		if result.find('wget returned 4') != -1: # probably no network adaptor connected
			self.feeds = 'NONETWORK'
			self.Stage2Completed = True
		if result.find('wget returned 8') != -1 or result.find('wget returned 1') != -1 or result.find('wget returned 255') != -1 or result.find('404 Not Found') != -1: # Server issued an error response, or there was a wget generic error code.
			self.feeds = 'DOWN'
			self.Stage2Completed = True
		elif result.find('bad address') != -1: # probably DNS lookup failed
			self.feeds = 'BAD'
			self.Stage2Completed = True
		elif result.find('Collected errors') != -1: # none of the above errors. What condition requires this to loop? Maybe double key press.
			AddPopupWithCallback(self.Stage2,
								 _("A background update check is in progress, please try again."),
								 MessageBox.TYPE_INFO,
								 10,
								 NOPLUGINS
			)
		else:
			print('[BackupManager] Restoring Stage 2: Complete')
			self.feeds = 'OK'
			self.Stage2Completed = True

	def Stage3(self):
		print('[BackupManager] Restoring Stage 3: Kernel Version/Feeds Checks')
		if self.feeds == 'OK':
			print('[BackupManager] Restoring Stage 3: Feeds are OK')
			if path.exists('/tmp/backupkernelversion') and path.exists('/tmp/backupimageversion'):
				kernelversion = open('/tmp/backupkernelversion').read()
				imageversion = open('/tmp/backupimageversion').read()
				print('[BackupManager] Backup Image:', imageversion)
				print('[BackupManager] Current Image:', about.getVersionString())
				print('[BackupManager] Backup Kernel:', kernelversion)
				print('[BackupManager] Current Kernel:', currentkernelversion)
				if imageversion == about.getVersionString() or isRestorablePlugins(imageversion):
					# print('[BackupManager] Restoring Stage 3: Kernel Version is same as backup')
					self.kernelcheck = True
					self.Console.ePopen('opkg list-installed', self.Stage3Complete)
				else:
					print('[BackupManager] Restoring Stage 3: Kernel or Image Version does not match, exiting')
					self.kernelcheck = False
					self.Stage6()
			else:
				print('[BackupManager] Restoring Stage 3: Kernel or Image Version check failed')
				self.kernelcheck = False
				self.Stage6()
		elif self.feeds == 'NONETWORK':
			print('[BackupManager] Restoring Stage 3: No network connection, plugin restore not possible')
			self.kernelcheck = False
			AddPopupWithCallback(self.Stage6,
								 _("Your receiver is not connected to a network. Please check your network settings and try again."),
								 MessageBox.TYPE_INFO,
								 15,
								 NOPLUGINS
			)
		elif self.feeds == 'DOWN':
			print('[BackupManager] Restoring Stage 3: Feeds are down, plugin restore not possible')
			self.kernelcheck = False
			AddPopupWithCallback(self.Stage6,
								 _("Sorry the feeds are down for maintenance. Please try again later."),
								 MessageBox.TYPE_INFO,
								 15,
								 NOPLUGINS
			)
		elif self.feeds == 'BAD':
			print('[BackupManager] Restoring Stage 3: no network connection, plugin restore not possible')
			self.kernelcheck = False
			AddPopupWithCallback(self.Stage6,
								 _("Your receiver is not connected to the Internet. Please check your network settings and try again."),
								 MessageBox.TYPE_INFO,
								 15,
								 NOPLUGINS
			)
		else:
			print('[BackupManager] Restoring Stage 3: Feeds state is unknown aborting')
			self.Stage6()

	def Stage3Complete(self, result, retval, extra_args):
		plugins = []
		if path.exists('/tmp/ExtraInstalledPlugins') and self.kernelcheck:
			self.pluginslist = []
			for line in result.split('\n'):
				if line:
					parts = line.strip().split()
					plugins.append(parts[0])
			tmppluginslist = open('/tmp/ExtraInstalledPlugins', 'r').readlines()
			for line in tmppluginslist:
				if line:
					parts = line.strip().split()
					if len(parts) > 0 and parts[0] not in plugins:
						self.pluginslist.append(parts[0])

		if path.exists('/tmp/3rdPartyPlugins') and self.kernelcheck:
			self.pluginslist2 = []
			self.plugfiles = []
			self.thirdpartyPluginsLocation = " "
			if config.backupmanager.xtraplugindir.value:
				self.thirdpartyPluginsLocation = config.backupmanager.xtraplugindir.value
				self.thirdpartyPluginsLocation = self.thirdpartyPluginsLocation.replace(' ', '%20')
				self.plugfiles = self.thirdpartyPluginsLocation.split('/',3)
			elif path.exists('/tmp/3rdPartyPluginsLocation'):
				self.thirdpartyPluginsLocation = open('/tmp/3rdPartyPluginsLocation', 'r').readlines()
				self.thirdpartyPluginsLocation = "".join(self.thirdpartyPluginsLocation)
				self.thirdpartyPluginsLocation = self.thirdpartyPluginsLocation.replace('\n', '')
				self.thirdpartyPluginsLocation = self.thirdpartyPluginsLocation.replace(' ', '%20')
				self.plugfiles = self.thirdpartyPluginsLocation.split('/',3)
			tmppluginslist2 = open('/tmp/3rdPartyPlugins', 'r').readlines()
			available = None
			for line in tmppluginslist2:
				if line:
					parts = line.strip().split('_')
					if parts[0] not in plugins:
						ipk = parts[0]
						if path.exists(self.thirdpartyPluginsLocation):
							available = listdir(self.thirdpartyPluginsLocation)
						else:
							devmounts = []
							files = []
							self.plugfile = self.plugfiles[3]
							for dir in ["/media/%s/%s" %(media, self.plugfile)  for media in listdir("/media/") if path.isdir(path.join("/media/", media))]:
								if media != "autofs" or "net":
									devmounts.append(dir)
							if len(devmounts):
								for x in devmounts:
									if path.exists(x):
										self.thirdpartyPluginsLocation = x
										try:
											available = listdir(self.thirdpartyPluginsLocation)
											break
										except:
											continue
						if available:
							for file in available:
								if file:
									fileparts = file.strip().split('_')
									# 									print('FILE:',fileparts)
									# 									print('IPK:',ipk)
									if fileparts[0] == ipk:
										self.thirdpartyPluginsLocation = self.thirdpartyPluginsLocation.replace(' ', '%20')
										ipk = path.join(self.thirdpartyPluginsLocation, file)
										if path.exists(ipk):
											# 											print('IPK', ipk)
											self.pluginslist2.append(ipk)

		print('[BackupManager] Restoring Stage 3: Complete')
		self.Stage3Completed = True

	def Stage4(self):
		if len(self.pluginslist) or len(self.pluginslist2):
			if len(self.pluginslist):
				self.pluginslist = " ".join(self.pluginslist)
			else:
				self.pluginslist = ""
			if len(self.pluginslist2):
				self.pluginslist2 = " ".join(self.pluginslist2)
			else:
				self.pluginslist2 = ""
			print('[BackupManager] Restoring Stage 4: Plugins to restore (extra plugins)', self.pluginslist)
			print('[BackupManager] Restoring Stage 4: Plugins to restore (3rd party plugins)', self.pluginslist2)
			AddPopupWithCallback(self.Stage4Complete,
								 _("Do you want to restore your Enigma2 plugins ?"),
								 MessageBox.TYPE_YESNO,
								 15,
								 PLUGINRESTOREQUESTIONID
			)
		else:
			print('[BackupManager] Restoring Stage 4: plugin restore not required')
			self.Stage6()

	def Stage4Complete(self, answer=None):
		if answer is True:
			print('[BackupManager] Restoring Stage 4: plugin restore chosen')
			self.doPluginsRestore = True
			self.Stage4Completed = True
		elif answer is False:
			print('[BackupManager] Restoring Stage 4: plugin restore skipped by user')
			AddPopupWithCallback(self.Stage6,
								 _("Now skipping restore process"),
								 MessageBox.TYPE_INFO,
								 15,
								 NOPLUGINS
			)

	def Stage5(self):
		if self.doPluginsRestore:
			print('[BackupManager] Restoring Stage 5: starting plugin restore')
			print('[BackupManager] Console command: ', 'opkg install ' + self.pluginslist + ' ' + self.pluginslist2)
			self.Console.ePopen('opkg install ' + self.pluginslist + ' ' + self.pluginslist2, self.Stage5Complete)
		else:
			print('[BackupManager] Restoring Stage 5: plugin restore not requested')
			self.Stage6()

	def Stage5Complete(self, result, retval, extra_args):
		if result:
			print("[BackupManager] opkg install result:\n", result)
			self.didPluginsRestore = True
			self.Stage5Completed = True
			print('[BackupManager] Restoring Stage 5: Completed')

	def Stage6(self, result=None, retval=None, extra_args=None):
		self.Stage1Completed = True
		self.Stage2Completed = True
		self.Stage3Completed = True
		self.Stage4Completed = True
		self.Stage5Completed = True
		if self.didPluginsRestore or self.didSettingsRestore:
			print('[BackupManager] Restoring Completed rebooting')
			quitMainloop(2)
		else:
			print('[BackupManager] Restoring failed or canceled')
			self.close()

class BackupSelection(Screen):
	skin = """
		<screen name="BackupSelection" position="center,center" size="560,400">
			<ePixmap pixmap="buttons/red.png" position="0,0" size="140,40" alphatest="on"/>
			<ePixmap pixmap="buttons/green.png" position="140,0" size="140,40" alphatest="on"/>
			<ePixmap pixmap="buttons/yellow.png" position="280,0" size="140,40" alphatest="on"/>
			<widget source="key_red" render="Label" position="0,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#9f1313" transparent="1"/>
			<widget source="key_green" render="Label" position="140,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#1f771f" transparent="1"/>
			<widget source="key_yellow" render="Label" position="280,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#a08500" transparent="1"/>
			<widget name="checkList" position="5,50" size="550,250" transparent="1" scrollbarMode="showOnDemand"/>
		</screen>"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self.setTitle(_("Select files/folders to backup"))

		self["key_red"] = StaticText(_("Cancel"))
		self["key_green"] = StaticText(_("Save"))
		self["key_yellow"] = StaticText()

		self.selectedFiles = config.backupmanager.backupdirs.value
		defaultDir = '/'
		self.filelist = MultiFileSelectList(self.selectedFiles, defaultDir)
		self["checkList"] = self.filelist

		self["actions"] = ActionMap(["DirectionActions", "OkCancelActions", "ShortcutActions", "MenuActions"],
									{
									"cancel": self.exit,
									"red": self.exit,
									"yellow": self.changeSelectionState,
									"green": self.saveSelection,
									"ok": self.okClicked,
									"left": self.left,
									"right": self.right,
									"down": self.down,
									"up": self.up,
									"menu": self.exit,
									}, -1)
		if not self.selectionChanged in self["checkList"].onSelectionChanged:
			self["checkList"].onSelectionChanged.append(self.selectionChanged)
		self.onLayoutFinish.append(self.layoutFinished)

	def layoutFinished(self):
		idx = 0
		self["checkList"].moveToIndex(idx)
		self.selectionChanged()

	def selectionChanged(self):
		current = self["checkList"].getCurrent()
		if current[2] is True:
			self["key_yellow"].setText(_("Deselect"))
		else:
			self["key_yellow"].setText(_("Select"))

	def up(self):
		self["checkList"].up()

	def down(self):
		self["checkList"].down()

	def left(self):
		self["checkList"].pageUp()

	def right(self):
		self["checkList"].pageDown()

	def changeSelectionState(self):
		self["checkList"].changeSelectionState()
		self.selectedFiles = self["checkList"].getSelectedList()

	def saveSelection(self):
		self.selectedFiles = self["checkList"].getSelectedList()
		config.backupmanager.backupdirs.value = self.selectedFiles
		config.backupmanager.backupdirs.save()
		config.backupmanager.save()
		config.save()
		self.close(None)

	def exit(self):
		self.close(None)

	def okClicked(self):
		if self.filelist.canDescent():
			self.filelist.descent()

	def closeRecursive(self):
		self.close(True)


class XtraPluginsSelection(Screen):
	skin = """
		<screen name="BackupSelection" position="center,center" size="560,400">
			<ePixmap pixmap="buttons/red.png" position="0,0" size="140,40" alphatest="on"/>
			<ePixmap pixmap="buttons/green.png" position="140,0" size="140,40" alphatest="on"/>
			<ePixmap pixmap="buttons/yellow.png" position="280,0" size="140,40" alphatest="on"/>
			<widget source="key_red" render="Label" position="0,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#9f1313" transparent="1"/>
			<widget source="key_green" render="Label" position="140,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#1f771f" transparent="1"/>
			<widget source="key_yellow" render="Label" position="280,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#a08500" transparent="1"/>
			<widget name="checkList" position="5,50" size="550,250" transparent="1" scrollbarMode="showOnDemand"/>
		</screen>"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self.setTitle(_("Select extra packages folder"))

		self["key_red"] = StaticText(_("Cancel"))
		self["key_green"] = StaticText(_("Save"))

		defaultDir = config.backupmanager.backuplocation.value
		self.filelist = FileList(defaultDir, showFiles=True, matchingPattern='^.*.(ipk)')
		self["checkList"] = self.filelist

		self["actions"] = ActionMap(["DirectionActions", "OkCancelActions", "ShortcutActions", "MenuActions"],
									{
									"cancel": self.exit,
									"red": self.exit,
									"green": self.saveSelection,
									"ok": self.okClicked,
									"left": self.left,
									"right": self.right,
									"down": self.down,
									"up": self.up,
									"menu": self.exit,
									}, -1)
		if not self.selectionChanged in self["checkList"].onSelectionChanged:
			self["checkList"].onSelectionChanged.append(self.selectionChanged)
		self.onLayoutFinish.append(self.layoutFinished)

	def layoutFinished(self):
		idx = 0
		self["checkList"].moveToIndex(idx)
		self.setWindowTitle()
		self.selectionChanged()

	def setWindowTitle(self):
		self.setTitle(_("Select folder that contains plugins"))

	def selectionChanged(self):
		current = self["checkList"].getCurrent()[0]

	def up(self):
		self["checkList"].up()

	def down(self):
		self["checkList"].down()

	def left(self):
		self["checkList"].pageUp()

	def right(self):
		self["checkList"].pageDown()

	def saveSelection(self):
		filelist = str(self.filelist.getFileList())
		if filelist.find('.ipk') != -1:
			config.backupmanager.xtraplugindir.setValue(self.filelist.getCurrentDirectory())
			config.backupmanager.xtraplugindir.save()
			config.backupmanager.save()
			config.save()
			self.close(None)
		else:
			self.session.open(MessageBox, _("Please enter a folder that contains some packages."), MessageBox.TYPE_INFO, timeout=10)

	def exit(self):
		self.close(None)

	def okClicked(self):
		if self.filelist.canDescent():
			self.filelist.descent()

	def closeRecursive(self):
		self.close(True)

class VISIONBackupManagerMenu(Screen, ConfigListScreen):
	skin = """
	<screen name="VISIONBackupManagerMenu" position="center,center" size="560,550">
		<ePixmap pixmap="buttons/red.png" position="0,0" size="140,40" alphatest="on"/>
		<ePixmap pixmap="buttons/green.png" position="140,0" size="140,40" alphatest="on"/>
		<ePixmap pixmap="buttons/yellow.png" position="280,0" size="140,40" alphatest="on"/>
		<ePixmap pixmap="buttons/blue.png" position="420,0" size="140,40" alphatest="on"/>
		<widget name="key_red" position="0,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#9f1313" transparent="1"/>
		<widget name="key_green" position="140,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#1f771f" transparent="1"/>
		<widget name="key_yellow" position="280,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#a08500" transparent="1"/>
		<widget name="key_blue" position="420,0" zPosition="1" size="140,40" font="Regular;20" halign="center" valign="center" backgroundColor="#18188b" transparent="1"/>
		<widget name="HelpWindow" pixmap="buttons/vkey_icon.png" position="450,510" zPosition="1" size="1,1" transparent="1" alphatest="on"/>
		<widget source="VKeyIcon" render="Pixmap" pixmap="buttons/key_text.png" position="0,500" zPosition="1" size="35,25" transparent="1" alphatest="on">
			<convert type="ConditionalShowHide"/>
		</widget>
		<widget name="footnote" position="0,50" size="300,20" zPosition="1" font="Regular;20" halign="left" transparent="1" valign="top"/>
		<widget name="config" position="0,90" size="560,375" transparent="0" enableWrapAround="1" scrollbarMode="showOnDemand"/>
		<widget name="description" position="0,e-75" size="560,75" font="Regular;18" halign="center" valign="top" transparent="0" zPosition="1"/>
	</screen>"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self.skinName = "VISIONBackupManagerMenu"
		Screen.setTitle(self, _("Vision Backup Manager Setup"))
		self["actions"] = ActionMap(['SetupActions', 'ColorActions', 'VirtualKeyboardActions', "MenuActions"],
		{
			"ok": self.keySave,
			"cancel": self.keyCancel,
			"red": self.keyCancel,
			"green": self.keySave,
			"yellow": self.chooseFiles,
			"blue": self.chooseXtraPluginDir,
			'showVirtualKeyboard': self.KeyText,
			"menu": self.keyCancel,
		}, -2)

		self["key_red"] = Button(_("Cancel"))
		self["key_green"] = Button(_("OK"))
		self["key_yellow"] = Button(_("Choose files"))
		self["key_blue"] = Button(_("Choose local IPK's folder"))

		self.onChangedEntry = [ ]
		self.list = []
		ConfigListScreen.__init__(self, self.list, session = self.session, on_change = self.changedEntry)
		self.createSetup()

	def chooseFiles(self):
		self.session.openWithCallback(self.backupfiles_choosen,BackupSelection)

	def chooseXtraPluginDir(self):
		self.session.openWithCallback(self.backupfiles_choosen,XtraPluginsSelection)

	def backupfiles_choosen(self, ret):
		self.backupdirs = ' '.join(config.backupmanager.backupdirs.value)
		config.backupmanager.backupdirs.save()
		config.backupmanager.save()
		config.save()

	def createSetup(self):
		imparts = []
		for p in harddiskmanager.getMountedPartitions():
			if path.exists(p.mountpoint):
				d = path.normpath(p.mountpoint)
				m = d + '/', p.mountpoint
				if p.mountpoint != '/':
					imparts.append((d + '/', p.mountpoint))

		config.backupmanager.backuplocation.setChoices(imparts)
		self.editListEntry = None
		self.list = []
		self.list.append(getConfigListEntry(_("Backup Location"), config.backupmanager.backuplocation))
		self.list.append(getConfigListEntry(_("Folder Prefix"), config.backupmanager.folderprefix))
		self.list.append(getConfigListEntry(_("Schedule Backups"), config.backupmanager.schedule))
		if config.backupmanager.schedule.value:
			self.list.append(getConfigListEntry(_("Time of Backup to start in minutes"), config.backupmanager.scheduletime))
			self.list.append(getConfigListEntry(_("Repeat how often"), config.backupmanager.repeattype))
		self["config"].list = self.list
		self["config"].setList(self.list)

	def changedEntry(self):
		if self["config"].getCurrent()[0] == _("Schedule Backups"):
			self.createSetup()
		for x in self.onChangedEntry:
			x()

	def getCurrentEntry(self):
		return self["config"].getCurrent()

	def KeyText(self):
		if self['config'].getCurrent():
			if self['config'].getCurrent()[0] == _("Folder Prefix"):
				from Screens.VirtualKeyBoard import VirtualKeyBoard
				self.session.openWithCallback(self.VirtualKeyBoardCallback, VirtualKeyBoard, title = self["config"].getCurrent()[0], text = self["config"].getCurrent()[1].getValue())

	def VirtualKeyBoardCallback(self, callback = None):
		if callback is not None and len(callback):
			self["config"].getCurrent()[1].setValue(callback)
			self["config"].invalidate(self["config"].getCurrent())

	def saveAll(self):
		for x in self["config"].list:
			x[1].save()
		config.save()

	def keySave(self):
		self.saveAll()
		self.close()

	def cancelConfirm(self, result):
		if not result:
			return

		for x in self["config"].list:
			x[1].cancel()
		self.close()

	def keyCancel(self):
		if self["config"].isChanged():
			self.session.openWithCallback(self.cancelConfirm, MessageBox, _("Really close without saving settings?"))
		else:
			self.close()

class VISIONBackupManagerLogView(Screen):
	skin = """
<screen name="VISIONBackupManagerLogView" position="center,center" size="560,400">
	<widget name="list" position="0,0" size="560,400" font="Regular;16"/>
</screen>"""

	def __init__(self, session, filename):
		self.session = session
		Screen.__init__(self, session)
		self.setTitle(_("Logs"))

		filedate = str(date.fromtimestamp(stat(filename).st_mtime))
		backuplog = _('Backup created') + ': ' + filedate + '\n\n'
		tar = tarfile.open(filename, "r")
		contents = ""
		for tarinfo in tar:
			file = tarinfo.name
			contents += str(file) + '\n'
		tar.close()
		backuplog = backuplog + contents

		self["list"] = ScrollLabel(str(backuplog))
		self["setupActions"] = ActionMap(["SetupActions", "ColorActions", "DirectionActions", "MenuActions"],
										 {
										 "cancel": self.cancel,
										 "ok": self.cancel,
										 "up": self["list"].pageUp,
										 "down": self["list"].pageDown,
										 "menu": self.closeRecursive,
										 }, -2)

	def cancel(self):
		self.close()

	def closeRecursive(self):
		self.close(True)

class AutoBackupManagerTimer:
	def __init__(self, session):
		self.session = session
		self.backuptimer = eTimer()
		self.backuptimer.callback.append(self.BackuponTimer)
		self.backupactivityTimer = eTimer()
		self.backupactivityTimer.timeout.get().append(self.backupupdatedelay)
		now = int(time())
		global BackupTime
		if config.backupmanager.schedule.value:
			print("[BackupManager] Backup Schedule Enabled at ", strftime("%c", localtime(now)))
			if now > 1262304000:
				self.backupupdate()
			else:
				print("[BackupManager] Backup Time not yet set.")
				BackupTime = 0
				self.backupactivityTimer.start(36000)
		else:
			BackupTime = 0
			print("[BackupManager] Backup Schedule Disabled at", strftime("(now=%c)", localtime(now)))
			self.backupactivityTimer.stop()

	def backupupdatedelay(self):
		self.backupactivityTimer.stop()
		self.backupupdate()

	def getBackupTime(self):
		backupclock = config.backupmanager.scheduletime.value
#
# Work out the time of the *NEXT* backup - which is the configured clock
# time on the nth relevant day after the last recorded backup day.
# The last backup time will have been set as 12:00 on the day it
# happened. All we use is the actual day from that value.
#
		lastbkup_t = int(config.backupmanager.lastbackup.value)
		if config.backupmanager.repeattype.value == "daily":
			nextbkup_t = lastbkup_t + 24*3600
		elif config.backupmanager.repeattype.value == "weekly":
			nextbkup_t = lastbkup_t + 7*24*3600
		elif config.backupmanager.repeattype.value == "monthly":
			nextbkup_t = lastbkup_t + 30*24*3600
		nextbkup = localtime(nextbkup_t)
		return int(mktime((nextbkup.tm_year, nextbkup.tm_mon, nextbkup.tm_mday, backupclock[0], backupclock[1], 0, nextbkup.tm_wday, nextbkup.tm_yday, nextbkup.tm_isdst)))

	def backupupdate(self, atLeast=0):
		self.backuptimer.stop()
		global BackupTime
		BackupTime = self.getBackupTime()
		now = int(time())
		if BackupTime > 0:
			if BackupTime < now + atLeast:
# Backup missed - run it 60s from now
				self.backuptimer.startLongTimer(60)
				print("[BackupManager] Backup Time overdue - running in 60s")
			else:
# Backup in future - set the timer...
				delay = BackupTime - now
				self.backuptimer.startLongTimer(delay)
		else:
			BackupTime = -1
		print("[BackupManager] Backup Time set to", strftime("%c", localtime(BackupTime)), strftime("(now=%c)", localtime(now)))
		return BackupTime

	def backupstop(self):
		self.backuptimer.stop()

	def BackuponTimer(self):
		self.backuptimer.stop()
		now = int(time())
		wake = self.getBackupTime()
		# If we're close enough, we're okay...
		atLeast = 0
		if wake - now < 60:
			print("[BackupManager] Backup onTimer occured at", strftime("%c", localtime(now)))
			from Screens.Standby import inStandby
# Check for querying enabled
			if not inStandby and config.backupmanager.query.value:
				message = _("Your receiver is about to run a backup of your settings and to detect your plugins.\nDo you want to allow this?")
				ybox = self.session.openWithCallback(self.doBackup, MessageBox, message, MessageBox.TYPE_YESNO, timeout=30)
				ybox.setTitle('Scheduled backup.')
			else:
				print("[BackupManager] in standby or no querying, so just running backup", strftime("%c", localtime(now)))
				self.doBackup(True)
		else:
			print('[BackupManager] We are not close enough', strftime("%c", localtime(now)))
			self.backupupdate(60)

	def doBackup(self, answer):
		now = int(time())
		if answer is False:
			if config.backupmanager.backupretrycount.value < 2:
				print('[BackupManager] Number of retries', config.backupmanager.backupretrycount.value)
				print("[BackupManager] Backup delayed.")
				repeat = config.backupmanager.backupretrycount.value
				repeat += 1
				config.backupmanager.backupretrycount.value = repeat
				BackupTime = now + (int(config.backupmanager.backupretry.value) * 60)
				print("[BackupManager] Backup Time now set to", strftime("%c", localtime(BackupTime)), strftime("(now=%c)", localtime(now)))
				self.backuptimer.startLongTimer(int(config.backupmanager.backupretry.value) * 60)
			else:
				atLeast = 60
				print("[BackupManager] Enough Retries, delaying till next schedule.", strftime("%c", localtime(now)))
				self.session.open(MessageBox, _("Enough retries, delaying till next schedule."), MessageBox.TYPE_INFO, timeout=10)
				config.backupmanager.backupretrycount.value = 0
				self.backupupdate(atLeast)
		else:
			print("[BackupManager] Running Backup", strftime("%c", localtime(now)))
			self.BackupFiles = BackupFiles(self.session)
			Components.Task.job_manager.AddJob(self.BackupFiles.createBackupJob())
# Note that fact that the job has been *scheduled*.
# We do *not* only note a successful completion, as that would result
# in a loop on issues such as disk-full.
# Also all that we actually want to know is the day, not the time, so we
# actually remember midday, which avoids problems around DLST changes
# for backups scheduled within an hour of midnight.
#
			sched = localtime(time())
			sched_t = int(mktime((sched.tm_year, sched.tm_mon, sched.tm_mday, 12, 0, 0, sched.tm_wday, sched.tm_yday, sched.tm_isdst)))
			config.backupmanager.lastbackup.value = sched_t
			config.backupmanager.lastbackup.save()

class BackupFiles(Screen):
	def __init__(self, session, updatebackup=False, imagebackup=False):
		Screen.__init__(self, session)
		self.Console = Console()
		self.updatebackup = updatebackup
		self.imagebackup = imagebackup
		self.BackupDevice = config.backupmanager.backuplocation.value
		print("[BackupManager] Device: " + self.BackupDevice)
		self.BackupDirectory = config.backupmanager.backuplocation.value + 'backup/'
		print("[BackupManager] Directory: " + self.BackupDirectory)
		self.Stage1Completed = False
		self.Stage2Completed = False
		self.Stage3Completed = False
		self.Stage4Completed = False
		self.Stage5Completed = False

	def createBackupJob(self):
		job = Components.Task.Job(_("Backup manager"))

		task = Components.Task.PythonTask(job, _("Starting..."))
		task.work = self.JobStart
		task.weighting = 1

		task = Components.Task.ConditionTask(job, _("Starting..."), timeoutCount=30)
		task.check = lambda: self.Stage1Completed
		task.weighting = 1

		task = Components.Task.PythonTask(job, _("Creating list of installed plugins..."))
		task.work = self.Stage2
		task.weighting = 1

		task = Components.Task.ConditionTask(job, _("Creating list of installed plugins..."), timeoutCount=30)
		task.check = lambda: self.Stage2Completed
		task.weighting = 1

		task = Components.Task.PythonTask(job, _("Backing up files..."))
		task.work = self.Stage3
		task.weighting = 1

		task = Components.Task.ConditionTask(job, _("Backing up files..."), timeoutCount=600)
		task.check = lambda: self.Stage3Completed
		task.weighting = 1

		task = Components.Task.PythonTask(job, _("Preparing extra plugins..."))
		task.work = self.Stage4
		task.weighting = 1

		task = Components.Task.ConditionTask(job, _("Preparing extra plugins..."), timeoutCount=600)
		task.check = lambda: self.Stage4Completed
		task.weighting = 1

		task = Components.Task.PythonTask(job, _("Backing up files..."))
		task.work = self.Stage5
		task.weighting = 1

		task = Components.Task.ConditionTask(job, _("Backing up files..."), timeoutCount=600)
		task.check = lambda: self.Stage5Completed
		task.weighting = 1

		task = Components.Task.PythonTask(job, _("Backup complete..."))
		task.work = self.BackupComplete
		task.weighting = 1

		return job

	def JobStart(self):
		self.selectedFiles = config.backupmanager.backupdirs.value
		if path.exists('/etc/wpa_supplicant.ath0.conf') and '/etc/wpa_supplicant.ath0.conf' not in self.selectedFiles:
			self.selectedFiles.append('/etc/wpa_supplicant.ath0.conf')
		if path.exists('/etc/wpa_supplicant.wlan0.conf') and '/etc/wpa_supplicant.wlan0.conf' not in self.selectedFiles:
			self.selectedFiles.append('/etc/wpa_supplicant.wlan0.conf')
		if path.exists('/etc/auto.network') and '/etc/auto.network' not in self.selectedFiles:
			self.selectedFiles.append('/etc/auto.network')
		if path.exists('/usr/crossepg/crossepg.config') and '/usr/crossepg/crossepg.config' not in self.selectedFiles:
			self.selectedFiles.append('/usr/crossepg/crossepg.config')
		if path.exists('/usr/crossepg/providers') and '/usr/crossepg/providers' not in self.selectedFiles:
			self.selectedFiles.append('/usr/crossepg/providers')
		if path.exists('/usr/lib/sabnzbd') and '/usr/lib/sabnzbd' not in self.selectedFiles:
			self.selectedFiles.append('/usr/lib/sabnzbd')
		if path.exists('/etc/samba') and '/etc/samba' not in self.selectedFiles:
			self.selectedFiles.append('/etc/samba')
		if path.exists('/usr/keys') and '/etc/CCcam.cfg' not in self.selectedFiles:
			self.selectedFiles.append('/usr/keys')
		if path.exists('/opt') and '/opt' not in self.selectedFiles:
			self.selectedFiles.append('/opt')
		if path.exists('/usr/script') and '/usr/script' not in self.selectedFiles:
			self.selectedFiles.append('/usr/script')
		if path.exists('/usr/sundtek') and '/usr/sundtek' not in self.selectedFiles:
			self.selectedFiles.append('/usr/sundtek')
		if path.exists('/etc/rc3.d/S99tuner.sh') and '/etc/rc3.d/S99tuner.sh' not in self.selectedFiles:
			self.selectedFiles.append('/etc/rc3.d/S99tuner.sh')
		if path.exists('/usr/lib/enigma2/python/Plugins/SystemPlugins/AutoBouquetsMaker/custom/favourites.xml') and '/usr/lib/enigma2/python/Plugins/SystemPlugins/AutoBouquetsMaker/custom/favourites.xml' not in self.selectedFiles:
			self.selectedFiles.append('/usr/lib/enigma2/python/Plugins/SystemPlugins/AutoBouquetsMaker/custom/favourites.xml')
		if path.exists('/usr/lib/enigma2/python/Plugins/SystemPlugins/AutoBouquetsMaker/providers/terrestrial_finder.xml') and '/usr/lib/enigma2/python/Plugins/SystemPlugins/AutoBouquetsMaker/providers/terrestrial_finder.xml' not in self.selectedFiles:
			self.selectedFiles.append('/usr/lib/enigma2/python/Plugins/SystemPlugins/AutoBouquetsMaker/providers/terrestrial_finder.xml')
		if path.exists('/usr/lib/enigma2/python/Plugins/SystemPlugins/AutoBouquetsMaker/custom'):
			for custommix in glob.glob('/usr/lib/enigma2/python/Plugins/SystemPlugins/AutoBouquetsMaker/custom/*CustomMix.xml'):
				if custommix not in self.selectedFiles:
					self.selectedFiles.append(custommix)

		config.backupmanager.backupdirs.setValue(self.selectedFiles)
		config.backupmanager.backupdirs.save()
		configfile.save()

		try:
			if not path.exists(self.BackupDirectory):
				mkdir(self.BackupDirectory, 0755)
		except Exception as e:
			print(str(e))
			print("[BackupManager] Device: " + config.backupmanager.backuplocation.value + ", i don't seem to have write access to this device.")

		s = statvfs(self.BackupDevice)
		free = (s.f_bsize * s.f_bavail) / (1024 * 1024)
		if int(free) < 50:
			self.session.open(MessageBox, _("The backup location does not have enough free space."), MessageBox.TYPE_INFO, timeout=10)
		else:
			self.Stage1Complete()

	def Stage1Complete(self):
		self.Stage1Completed = True

	def Stage2(self):
		now = datetime.now()
		open('/var/log/backupmanager.log', 'w').write(now.strftime("%Y-%m-%d %H:%M") + ": Backup started\n")
		self.backupdirs = ' '.join(config.backupmanager.backupdirs.value)
		print('[BackupManager] Listing installed plugins')
		self.Console.ePopen('opkg status', self.Stage2Complete)

	def Stage2Complete(self, result, retval, extra_args):
		if result:
			plugins_out = []
			opkg_status_list = result.split('\n\n')
			for opkg_status in opkg_status_list:
				plugin = ''
				opkg_status_split = opkg_status.split('\n')
				for line in opkg_status_split:
					if line.startswith('Package'):
						parts = line.strip().split()
						if len(parts) > 1 and parts[1] not in ('opkg', 'openvision-base'):
							plugin = parts[1]
							continue
					if plugin and line.startswith('Status') and 'user installed' in line:
						plugins_out.append(plugin)
						break
			open('/tmp/ExtraInstalledPlugins', 'w').write('\n'.join(plugins_out))

		if path.exists('/tmp/ExtraInstalledPlugins'):
			print('[BackupManager] Listing completed.')
			self.Stage2Completed = True
		else:
			self.session.openWithCallback(self.BackupComplete, MessageBox, _("Plugin listing failed - e. g. wrong backup destination or no space left on backup device."), MessageBox.TYPE_INFO, timeout=10)
			print('[BackupManager] Result.', result)
			print("{BackupManager] Plugin listing failed - e. g. wrong backup destination or no space left on backup device")

	def Stage3(self):
		print('[BackupManager] Finding kernel version:' + currentkernelversion)
		open('/tmp/backupkernelversion', 'w').write(currentkernelversion)
		print('[BackupManager] Finding image version:' + about.about.getVersionString())
		open('/tmp/backupimageversion', 'w').write(about.about.getVersionString())
		self.Stage3Completed = True

	def Stage4(self):
		if config.backupmanager.xtraplugindir.value and path.exists(config.backupmanager.xtraplugindir.value):
			output = open('/tmp/3rdPartyPlugins', 'w')
			for file in listdir(config.backupmanager.xtraplugindir.value):
				if file.endswith('.ipk'):
					parts = file.strip().split('_')
					output.write(parts[0] + '\n')
					output.close()
			open('/tmp/3rdPartyPluginsLocation', 'w').write(config.backupmanager.xtraplugindir.value)
		self.Stage4Completed = True

# Filename for backup list
	tar_flist = "/tmp/_backup-files.list"
	def Stage5(self):
		tmplist = config.backupmanager.backupdirs.value
		tmplist.append('/tmp/ExtraInstalledPlugins')
		tmplist.append('/tmp/backupkernelversion')
		tmplist.append('/tmp/backupimageversion')
		if path.exists('/tmp/3rdPartyPlugins'):
			tmplist.append('/tmp/3rdPartyPlugins')
		if path.exists('/tmp/3rdPartyPluginsLocation'):
			tmplist.append('/tmp/3rdPartyPluginsLocation')
		self.backupdirs = ' '.join(tmplist)
		print('[BackupManager] Backup running')
		backupdate = datetime.now()
		backupType = "-"
		if self.updatebackup:
			backupType = "-SU-"
		elif self.imagebackup:
			backupType = "-IM-"
		self.Backupfile = self.BackupDirectory + config.backupmanager.folderprefix.value + '-' + getImageDistro() + backupType + getVisionVersion() + '-' + getVisionRevision() + '-' + getBoxType() + '-' + backupdate.strftime("%Y%m%d-%H%M") + '.tar.gz'
# Need to create a list of what to backup, so that spaces and special
# characters don't get lost on, or mangle, the command line
#
		with open(BackupFiles.tar_flist, "w") as tfl:
			for fn in tmplist:
				tfl.write(fn + "\n")
		self.Console.ePopen("tar -T " + BackupFiles.tar_flist + " -czvf " + self.Backupfile, self.Stage4Complete)

	def Stage4Complete(self, result, retval, extra_args):
		if path.exists(self.Backupfile):
			chmod(self.Backupfile, 0644)
			print('[BackupManager] Complete.')
			remove('/tmp/ExtraInstalledPlugins')
			self.Stage5Completed = True
		else:
			self.session.openWithCallback(self.BackupComplete, MessageBox, _("Backup failed - e. g. wrong backup destination or no space left on backup device."), MessageBox.TYPE_INFO, timeout=10)
			print('[BackupManager] Result.', result)
			print("{BackupManager] Backup failed - e. g. wrong backup destination or no space left on backup device")
# Delete the list of backup files now that it's finished.
# Ignore any failure here, as there's nothing useful we could do anyway...
		try:
			remove(BackupFiles.tar_flist)
		except:
			pass

	def BackupComplete(self):
		self.Stage1Completed = True
		self.Stage2Completed = True
		self.Stage3Completed = True
		self.Stage4Completed = True
		self.Stage5Completed = True

# Trim the number of backups to the configured setting...
#
		try:
			if config.backupmanager.number_to_keep.value > 0 \
			 and path.exists(self.BackupDirectory): # !?!
				images = listdir(self.BackupDirectory)
# Only try to delete backups with the current user prefix
				emlist = []
				for fil in images:
					if (fil.startswith(config.backupmanager.folderprefix.value) and fil.endswith(".tar.gz")):
						emlist.append(fil)
# sort by oldest first...
				emlist.sort(key=lambda fil: path.getmtime(self.BackupDirectory + fil))
# ...then, if we have too many, remove the <n> newest from the end
# and delete what is left
				if len(emlist) > config.backupmanager.number_to_keep.value:
					emlist = emlist[0:len(emlist)-config.backupmanager.number_to_keep.value]
					for fil in emlist:
						remove(self.BackupDirectory + fil)
	    	except:
	    		pass

		if config.backupmanager.schedule.value:
			atLeast = 60
			autoBackupManagerTimer.backupupdate(atLeast)
		else:
			autoBackupManagerTimer.backupstop()
