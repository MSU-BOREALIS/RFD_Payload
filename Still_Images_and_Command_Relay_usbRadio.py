#!/usr/bin/env python
"""
Still Images and Command Relay for Raspberry Pi with Xbee and RFD 900+

Author:	Austin Langford, AEM, MnSGC
Based on work from the Montana Space Grant Consortium
Software created for use by the Minnesota Space Grant Consortium
Purpose: To communicate with a ground transceiver to receive commands, and relay them through the xbee                                                    #
Additional Features: GPS beacon and Image Transmission
Creation Date: March 2016
"""

import time
import threading, Queue
from time import strftime
import datetime
import io
import picamera
import subprocess
import serial
import sys
import os
import base64
import hashlib
import serial.tools.list_ports

os.system('modprobe w1-gpio')
os.system('modprobe w1-therm')

temp_sensor = '/sys/bus/w1/devices/28-00000522ec61/w1_slave'

rfdPort = serial.Serial(port = "/dev/ttyAMA0", baudrate = 38400, timeout = 5)
gpsPort = serial.Serial(port = '/dev/gps', baudrate = 9600, timeout = 3)

class GPSThread(threading.Thread):
    """ A thread to read in raw GPS information, and organize it for the main thread """
    def __init__(self, threadID, gps, Q, exceptions, resetFlag,loggingGPS):			# Constructor
        threading.Thread.__init__(self)
        self.threadID = threadID
        self.gpsSer = gps
        self.gpsQ = Q
        self.exceptionsQ = exceptions
        self.resetFlagQ = resetFlag
        self.loggingGPS = loggingGPS

    def run(self):
        global folder
        try:
            while True:					# Run forever
                line = self.gpsSer.readline()
                if(line.find("GPGGA") != -1):		# GPGGA indicates it's the GPS stuff we're looking for
                    try:
                        ### Parse the GPS Info ###
                        line = line.split(',')
                        if(line[1] == ''):
                            hours = 0
                            minutes = 0
                            seconds = 0
                        else:
                            hours = int(line[1][0:2])
                            minutes = int(line[1][2:4])
                            seconds = int(line[1][4:].split('.')[0])
                        if(line[2] == ''):
                            lat = 0
                        else:
                            lat = float(line[2][0:2]) + (float(line[2][2:]))/60
                        if(line[4] == ''):
                            lon = 0
                        else:
                            lon = -(float(line[4][0:3]) + (float(line[4][3:]))/60)
                        if(line[9] == ''):
                            alt = 0
                        else:
                            alt = float(line[9])
                        sat = int(line[7])

                        ### Organize the GPS info, and put it in the queue ###
                        gpsStr = str(hours)+','+ str(minutes)+','+ str(seconds)+','+ str(lat)+','+str(lon)+','+str(alt)+','+str(sat)+'!'+'\n'
                        self.gpsQ.put(gpsStr)

                        if self.loggingGPS:
                            try:
                                f = open(folder+"gpslog.txt","a")
                                f.write(gpsStr)
                                f.close()
                            except Exception, e:
                                print("Error logging GPS")
                                self.exceptionsQ.put(str(e))

                    except Exception,e:
                        self.exceptionsQ.put(str(e))

        ### Catches unexpected errors ###
        except Exception, e:
            self.exceptionsQ.put(str(e))
            self.resetFlagQ.put('gpsThread dead')

class TempThread(threading.Thread):
    """ A thread to read in raw temperature sensor information, and organize it for the main thread """
    def __init__(self, threadID, Q, exceptions, resetFlag, loggingTemp):			# Constructor
        threading.Thread.__init__(self)
        self.threadID = threadID
        self.tempQ = Q
        self.exceptionsQ = exceptions
        self.resetFlagQ = resetFlag
        self.loggingTemp = loggingTemp

    def temp_raw(self):
        f = open(temp_sensor,'r')
        lines = f.readlines()
        f.close()
        return lines

    def read_temp(self):
        lines = self.temp_raw()
        temp_output = lines[1].find('t=')
        if temp_output != 1:
            temp_string = lines[1].strip()[temp_output+2:]
            temp_c = float(temp_string)/1000.0
            temp_f = temp_c*9.0/5.0 + 32.0
            return temp_c, temp_f

    def run(self):
        global folder
        try:
            while True:					# Run forever
                temp = self.read_temp()
                self.tempQ.put(temp)

                if self.loggingTemp:
                    try:
                        curTime = str(datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S"))
                        f = open(folder+"templog.txt","a")
                        f.write(curTime+" - "+str(temp)+"\n")
                        f.close()
                    except Exception, e:
                        print(str(e))
                        self.exceptionsQ.put(str(e))

        ### Catches unexpected errors ###
        except Exception, e:
            self.exceptionsQ.put(str(e))
            self.resetFlagQ.put('tempThread dead')

class XbeeReceiveThread(threading.Thread):
    """ A thread to read information from the xbee, and send it to the main thread """
    def __init__(self,threadID, xbee, xbeeReceived, exceptions, resetFlag):         # Constructor
        threading.Thread.__init__(self)
        self.threadID = threadID
        self.xbee = xbee
        self.receivedQ = xbeeReceived
        self.exceptionsQ = exceptions
        self.resetFlagQ = resetFlag

    def run(self):
        while self.xbee.isOpen():
            try:
                line = self.xbee.readline()         # Read a line from the xbee
                self.receivedQ.put(line)        # Put the information in the receivedQ
            except Exception, e:                    # Catch any unexpected error, and notify the main thread of them
                self.exceptionsQ.put(str(e))
                self.resetFlagQ.put('reset')


class XbeeSendThread(threading.Thread):
    """ A Thread to send information out through the xbee radio """
    def __init__(self,threadID, xbee, xbeeToSend,exceptions,resetFlag):         # Constructor
        threading.Thread.__init__(self)
        self.threadID = threadID
        self.xbee = xbee
        self.sendQ = xbeeToSend
        self.exceptionsQ = exceptions
        self.resetFlagQ = resetFlag

    def run(self):
        while self.xbee.isOpen():
            try:
                while(not self.sendQ.empty()):              # If there are items in the sendQ, send them out the xbee
                    temp = self.sendQ.get()
                    self.xbee.write(str(self.sendQ.get()).encode())

            except Exception, e:                            # Catch any unexpected error, and notify the main thread of them
                self.exceptionsQ.put(str(e))
                self.resetFlagQ.put('reset')

class TakePicture(threading.Thread):
    """ Thread to take two pictures, one at full resolution, the other at the selected resolution """
    def __init__(self, threadID, cameraSettings,folder,imagenumber,picQ):        # Constructor
        threading.Thread.__init__(self)
        self.threadID = threadID
        self.cameraSettings = cameraSettings
        self.folder = folder
        self.imagenumber = imagenumber
        self.q = picQ

    def run(self):

        ### Open the camera ###
        try:
            camera = picamera.PiCamera()
        except:
            self.q.put('No Cam')        # If the camera fails to open, make sure the loop gets notified
            return

        try:
            settings = self.cameraSettings.getSettings()
            width = settings[0]
            height = settings[1]
            sharpness = settings[2]
            brightness = settings[3]
            contrast = settings[4]
            saturation = settings[5]
            iso = settings[6]
            # Setup the camera with the settings read previously
            camera.sharpness = sharpness
            camera.brightness = brightness
            camera.contrast = contrast
            camera.saturation = saturation
            camera.iso = iso
            camera.resolution = (2592,1944)             # Default max resolution photo
            extension = '.png'
            camera.hflip = self.cameraSettings.getHFlip()
            camera.vflip = self.cameraSettings.getVFlip()

            camera.capture(self.folder+"%s%04d%s" %("image",self.imagenumber,"_a"+extension))     # Take the higher resolution picture
            print "( 2592 , 1944 ) photo saved"

            fh = open(self.folder+"imagedata.txt","a")              # Save the pictures to imagedata.txt
            fh.write("%s%04d%s @ time(%s) settings(w=%d,h=%d,sh=%d,b=%d,c=%d,sa=%d,i=%d)\n" % ("image",self.imagenumber,"_a"+extension,str(datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")),2592,1944,sharpness,brightness,contrast,saturation,iso))       # Add it to imagedata.txt
            camera.resolution = (width,height)          # Switch the resolution to the one set by the ground station
            extension = '.jpg'

            camera.capture(self.folder+"%s%04d%s" %("image",self.imagenumber,"_b"+extension))     # Take the lower resolution picture
            print "(",width,",",height,") photo saved"
            fh.write("%s%04d%s @ time(%s) settings(w=%d,h=%d,sh=%d,b=%d,c=%d,sa=%d,i=%d)\n" % ("image",self.imagenumber,"_b"+extension,str(datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")),width,height,sharpness,brightness,contrast,saturation,iso))       # Add it to imagedata.txt
            print "settings file updated"
            self.q.put('done')
        except Exception, e:                                         # If there's any errors while taking the picture, reset the checkpoint
            print(str(e))
            self.q.put('checkpoint')

        finally:
            try:
                camera.close()
                fh.close()
            except:
                pass

class Unbuffered:
    """ Helps eliminate the serial buffer, also logs all print statements to the logfile """
    def __init__(self,stream):
        self.stream = stream
    def write(self,data):
        self.stream.write(data)
        self.stream.flush()
        logfile.write(data)
        logfile.flush()

class CameraSettings:
    """ A class to handle camera settings """
    def __init__(self,width,height,sharpness,brightness,contrast,saturation,iso):
        self.width = width
        self.height = height
        self.resolution = (width,height)
        self.sharpness = sharpness
        self.brightness = brightness
        self.contrast = contrast
        self.saturation = saturation
        self.iso = iso
        self.hflip = False
        self.vflip = False

    def getSettings(self):
        return [self.width, self.height, self.sharpness, self.brightness, self.contrast, self.saturation ,self.iso]

    def getSettingsString(self):
        return str(self.width) + ',' + str(self.height) + ',' + str(self.sharpness) + ',' + str(self.brightness) + ',' + str(self.contrast) + ',' + str(self.saturation) + ',' + str(self.iso)

    def setCameraAnnotation(self,annotation):
        self.annotation = annotation

    def getCameraAnnotation(self):
        return self.annotation

    def getHFlip(self):
        return self.hflip

    def getVFlip(self):
        return self.vflip

    def toggleHorizontalFlip(self):
        if(self.hflip == False):
                self.hflip = True
        else:
                self.hflip = False
        return self.hflip

    def toggleVerticalFlip(self):
        if(self.vflip == False):
                self.vflip = True
        else:
                self.vflip = False
        return self.vflip

    def newSettings(self,settings):
        self.width = int(settings[0])
        self.height = int(settings[1])
        self.sharpness = int(settings[2])
        self.brightness = int(settings[3])
        self.contrast = int(settings[4])
        self.saturation = int(settings[5])
        self.iso = int(settings[6])
        self.resolution = (self.width,self.height)

class main:

    """ The main program class """
    def __init__(self):
        global folder, loggingGPS, loggingTemp
        self.folder = folder

        # Get a list of the usb devices connected and assign them properly
        ports = serial.tools.list_ports.comports()
        serialConverters = []
        for each in ports:
            print(each.vid,each.pid,each.hwid,each.device)
            if each.vid == 1027 and each.pid == 24597:
                self.xPort = each.device
                print('Xbee on ' + str(each.device))

            elif each.vid == 1659 and each.pid == 8963:
                serialConverters.append(each)

        # The USB-serial converters don't have unique IDs, so they need to be checked
        for each in serialConverters:
            gpsTest = serial.Serial(port = each.device, baudrate = 9600, timeout = 1)
            try:
                sample = gpsTest.readline()
                print(sample)
                sample = gpsTest.readline()     # Get 2 lines to make sure it's a full line
                print(sample)
                print(sample[0:2])
                if sample[0:2] == "$G":
                    self.gpsPort = gpsPort
                    print('GPS on ' + str(each.device))
                else:
                    self.rfdPort = rfdPort
                    print('RFD on ' + str(each.device))
            except Exception, e:
                print(str(e))

        ### Serial Port Initializations ###
        #RFD900 Serial Variables
        self.rfdBaud = 38400
        self.rfdTimeout = 3

        # GPS Serial Variables
        self.gpsBaud = 9600
        self.gpsTimeout = 3

        # XBee Serial Variables
        self.xBaud = 9600
        self.xTimeout = 3

        # Create the imagedata.txt file
        fh = open(self.folder + "imagedata.txt","w")
        fh.write("")
        fh.close()

        ### Picture Variables ###
        self.wordlength = 7000
        self.imagenumber = 0
        self.recentimg = ""
        self.pic_interval = 60
        self.cameraSettings = CameraSettings(650,450,0,50,0,0,100)
        self.reset_cam()
        self.takingPicture = False

        ### Create queues to share info with the threads
        self.xSendQ = Queue.Queue()
        self.xReceivedQ = Queue.Queue()
        self.xReceivedExceptionsQ = Queue.Queue()
        self.xSendExceptionsQ = Queue.Queue()
        self.xReceivedResetQ = Queue.Queue()
        self.xSendResetQ = Queue.Queue()
        self.gpsQ = Queue.LifoQueue()
        self.gpsExceptionsQ = Queue.Queue()
        self.gpsResetQ = Queue.Queue()
        self.tempQ = Queue.LifoQueue()
        self.tempExceptionsQ = Queue.Queue()
        self.tempResetQ = Queue.Queue()
        self.picQ = Queue.Queue()

        ### Try to create the various serial objects; if they fail to be created, set them as disabled ###
        # RFD 900
        try:
            self.ser = rfdPort = serial.Serial(port = "/dev/ttyAMA0", baudrate = 38400, timeout = 5)
            self.rfdEnabled = True
        except:
            self.rfdEnabled = False
            print('RFD disabled')

        # Camera
        try:
            camera = picamera.PiCamera()
            camera.close()
            self.cameraEnabled = True
        except:
            self.cameraEnabled = False
            if(self.rfdEnabled):
                self.ser.write('Alert: Camera is disabled\n')
            print('Camera disabled')

        # Xbee
        try:
            self.xbee = serial.Serial(port = self.xPort, baudrate = self.xBaud, timeout = self.xTimeout)
            self.xbeeEnabled = True
        except:
            self.xbeeEnabled = False
            if(self.rfdEnabled):
                self.ser.write('Alert: Xbee is disabled\n')
            print('Xbee disabled')

        # GPS
        try:
            self.gps = gpsPort = serial.Serial(port = '/dev/gps', baudrate = 9600, timeout = 3)
            self.gpsEnabled = True
        except:
            self.gpsEnabled = False
            if(self.rfdEnabled):
                self.ser.write('Alert: GPS is disabled\n')
            print('GPS disabled')

        # Temp Sensor
        try:
            f = open(temp_sensor,'r')
            lines = f.readlines()
            f.close()
            if(lines[0].strip()[-3:] != 'YES'):
                self.tempEnabled = False
                if(self.rfdEnabled):
                    self.ser.write('Alert: Temp Sensor is disabled\n')
                print('Temp Sensor Disabled')
            else:
                self.tempEnabled = True

        except Exception, e:
            print(str(e))
            self.tempEnabled = False
            if(self.rfdEnabled):
                self.ser.write('Alert: Temp Sensor is disabled\n')
            print('Temp Sensor Disabled')

        ### Start the appropriate side threads ###
        # Xbee
        if(self.xbeeEnabled):
            self.startXbeeThreads()

        # GPS
        if(self.gpsEnabled):
            self.startGPSThread()

        # Temp Sensor
        if(self.tempEnabled):
            self.startTempThread()

        # Get Started
        self.starttime = time.time()
        print "Started at @ ",datetime.datetime.now()
        self.checkpoint = time.time()


    def getGPSCom(self):
        return [self.gpsPort,self.gpsBaud,self.gpsTimeout]

    def getXbeeCom(self):
        return [self.xPort,self.xBaud,self.xTimeout]

    def getRFDCom(self):
        return [self.rfdPort,self.rfdBaud,self.rfdTimeout]

    def reset_cam(self):
        """ Resets the camera to the default settings """
        self.cameraSettings = CameraSettings(650,450,0,50,0,0,100)

    def image_to_b64(self,path):
        """ Converts an image to 64 bit encoding """
        with open(path,"rb") as imageFile:
            return base64.b64encode(imageFile.read())

    def b64_to_image(self,data,savepath):
        """ Converts a 64 bit encoding to an image """
        fl = open(savepath,"wb")
        fl.write(data.decode('base4'))
        fl.close()

    def gen_checksum(self,data,pos):
        """ Creates a checksum based on data """
        return hashlib.md5(data[pos:pos+self.wordlength]).hexdigest()

    def sendword(self,data,pos):
        """ Sends the appropriately sized piece of the total picture encoding """
        if(pos + self.wordlength < len(data)):       # Take a piece of size self.wordlength from the whole, and send it
            self.ser.write(data[pos:pos+self.wordlength])
            return
        else:                                   # If the self.wordlength is greater than the amount remaining, send everything left
            self.ser.write(data[pos:pos+len(data)])
            return

    def sync(self):
        """ Synchronizes the data stream between the Pi and the ground station """
        synccheck = ''
        synctry = 5
        syncterm = time.time() + 10
        while((synccheck != 'S')&(syncterm > time.time())):
            self.ser.write("sync")
            synccheck = self.ser.read()
            if(synctry == 0):
                if (synccheck == ""):
                    print "SyncError"
                    break
            synctry -= 1
        time.sleep(0.5)
        return

    def send_image(self,exportpath):
        """ Sends the image through the RFD in increments of size self.wordlength """
        timecheck = time.time()
        done = False
        cur = 0
        trycnt = 0
        outbound = self.image_to_b64(exportpath)     # Determine where the encoded image is
        size = len(outbound)
        print size,": Image Size"
        print "photo request received"
        self.ser.write(str(size)+'\n')               # Send the total size so the ground station knows how big it will be
        while(cur < len(outbound)):
            print "Send Position:", cur," // Remaining:", int((size - cur)/1024), "kB"      # Print out how much picture is remaining in kilobytes
            checkours = self.gen_checksum(outbound,cur)      # Create the checksum to send for the ground station to compare to
            self.ser.write(checkours)
            self.sendword(outbound,cur)                      # Send a piece of size self.wordlength
            checkOK = self.ser.read()
            if (checkOK == 'Y'):                # This is based on whether or not the word was successfully received based on the checksums
                cur = cur + self.wordlength
                trycnt = 0
            else:
                if(trycnt < 5):                 # There are 5 tries to get the word through, each time you fail, drop the self.wordlength by 1000
                    if self.wordlength >1000:
                        self.wordlength -= 1000
                    self.sync()
                    trycnt += 1
                    print "try number:", trycnt
                    print "resending last @", cur
                    print "ours:",checkours
                    print "self.wordlength",self.wordlength
                else:
                    print "error out"
                    cur = len(outbound)
        print "Image Send Complete"
        print "Send Time =", (time.time() - timecheck)
        return

    def mostRecentImage(self):
        """ Command 1: Send most recent image """
        self.ser.write('A')      # Send the acknowledge
        try:
            print "Send Image Command Received"
            print "Sending:", self.recentimg
            self.ser.write(self.recentimg)
            self.send_image(self.folder+self.recentimg)            # Send the most recent image
            self.wordlength = 7000                       # Reset the self.wordlength in case it was changed while sending
        except:
            print "Send Recent Image Error"

    def sendImageData(self):
        """ Command 2: Sends imagedata.txt """
        self.ser.write('A')
        try:
            print "data list request recieved"
            f = open(self.folder+"imagedata.txt","r")
            print "Sending imagedata.txt"
            for line in f:
                self.ser.write(line)
            self.ser.write('X\n')
            f.close()
            time.sleep(1)
        except:
            print "Error with imagedata.txt read or send"

    def requestedImage(self):
        """ Sends the requested image """
        self.ser.write('A')
        try:
            print"specific photo request recieved"
            temp = self.ser.readline()
            self.ser.reset_input_buffer()
            killTime = time.time() + 5
            while(temp.split(';')[0] != "RQ" and time.time() < killTime):
                temp = self.ser.readline()
##            while(temp != 'B' and time.time() < killTime):
##                temp = self.ser.readline()
            imagetosend = temp.split(';')[1]
##            imagetosend = self.ser.read(15)                  # Determine which picture to send
            self.send_image(self.folder+imagetosend)
            self.wordlength = 7000
        except Exception, e:
            print str(e)

    def sendCameraSettings(self):
        """ Sends the camera settings """
        self.ser.write('Ack\n')
        try:
            print "Attempting to send camera settings"
            settingsStr = self.cameraSettings.getSettingsString()
            self.ser.write(settingsStr+'\n')
            print "Camera Settings Sent"
            self.ser.reset_input_buffer()
        except Exception, e:
            print(str(e))

    def getCameraSettings(self):
        """ Updates the camera settings """
        self.ser.write('Ack1\n')
        try:
            print "Attempting to update camera settings"
            self.ser.reset_input_buffer()
            killTime = time.time() + 10
            self.ser.reset_input_buffer()

            temp = self.ser.readline()
            while(temp.split('/')[0] != "RQ" and time.time() < killTime):
                self.ser.write('Ack1\n')
                temp = self.ser.readline()

            if time.time() > killTime:
                return
            else:
                self.ser.write('Ack2\n')
                settings = temp.split('/')[1]
                settingsLst = settings.split(',')
                fail = False
                if len(settingsLst) == 7:
                    for each in settingsLst:
                        try:
                            each = each.replace('\n','')
                            each = int(each)
                        except:
                            fail = True

                if fail == False:
                    self.cameraSettings.newSettings(settingsLst)
                    print "Camera Settings Updated"
                    self.ser.write('Ack2\n')
                    self.ser.reset_input_buffer()

        except Exception, e:
            print str(e)
            self.reset_cam()

    def timeSync(self):
        """ Sends the current time """
        self.ser.write('A')
        try:
            print "Time Sync Request Recieved"

            timeval=str(datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S"))+"\n"     # Send the data time down to the ground station
            for x in timeval:
                self.ser.write(x)
        except:
            print "error with time sync"

    def pingTest(self):
        """ Connection test, test ping time """
        self.ser.write('A')
        print "Ping Request Received"
        try:
            termtime = time.time() + 10
            pingread = self.ser.read()
            while ((pingread != 'D') &(pingread != "") & (termtime > time.time())):     # Look for the stop character D, no new info, or too much time passing
                if (pingread == '~'):       # Whenever you get the P, send one back and get ready for another
                    print "Ping Received"
                    self.ser.reset_input_buffer()
                    self.ser.write('~')
                else:                       # If you don't get the P, sne back an A instead
                    print "pingread = ",pingread
                    self.ser.reset_input_buffer()
                    self.ser.write('A')
                pingread = self.ser.read()       # Read the next character
                sys.stdin.flush()
        except:
            print "Ping Runtime Error"

    def sendPing(self):
        """ Sends a Ping when requested """
        try:
            termtime = time.time() + 10
            pingread = self.ser.read()
            while ((pingread != 'D') &(pingread != "") & (termtime > time.time())):     # Look for the stop character D, no new info, or too much time passing
                if (pingread == '~'):       # Whenever you get the P, send one back and get ready for another
                    print "Ping Received"
                    self.ser.reset_input_buffer()
                    self.ser.write('~')
                else:                       # If you don't get the P, sne back an A instead
                    print "pingread = ",pingread
                    self.ser.reset_input_buffer()
                    self.ser.write('A')
                pingread = self.ser.read()       # Read the next character
                sys.stdin.flush()
        except:
            print "Ping Runtime Error"

    def sendPiRuntime(self):
        """ Sends the runtimedata """
        self.ser.write('A')
        try:
            print "Attempting to send piruntimedata"
            f = open(self.folder+"piruntimedata.txt","r")     # Open the runtimedata file
            temp = f.readline()
            while(temp != ""):      # Send everyting in the file until it's empty
                self.ser.write(temp)
                temp = f.readline()
            f.close()
            print "piruntimedata.txt sent"
        except:
            print "error sending piruntimedata.txt"

    def horizontalFlip(self):
        """ Flips the pictures horizontally """
        self.ser.write('A')
        try:
            self.cameraSettings.toggleHorizontalFlip()
            print("Camera Flipped Horizontally")
        except:
            print("Error flipping image horizontally")

    def verticalFlip(self):
        """ Flips the pictures vertically """
        self.ser.write('A')
        try:
            self.cameraSettings.toggleVerticalFlip()
            print("Camera Flipped Vertically")
        except:
            print("Error flipping image vertically")

    def sendDeviceStatus(self):
        """ Returns the status of the serial devices to the ground station """
        try:
            status = 'Camera: '+str(self.cameraEnabled)+', GPS: '+str(self.gpsEnabled)+', Xbee: '+str(self.xbeeEnabled) +', Temp: '+self.recentTemp + '\n'
            self.ser.write(status)
            print('Status Sent')
        except Exception, e:
            print(str(e))


    def startXbeeThreads(self):
        # Xbee Receive Thread
        self.xReceiveThread = XbeeReceiveThread("xbeeReceivedThread",self.xbee, self.xReceivedQ, self.xReceivedExceptionsQ, self.xReceivedResetQ)
        self.xReceiveThread.daemon = True
        self.xReceiveThread.start()

        # Xbee Send Thread
        self.xSendThread = XbeeSendThread("xbeeSendThread", self.xbee, self.xSendQ, self.xSendExceptionsQ, self.xSendResetQ)
        self.xSendThread.daemon = True
        self.xSendThread.start()

    def startGPSThread(self):
        self.gpsThread = GPSThread("gpsThread",self.gps, self.gpsQ, self.gpsExceptionsQ, self.gpsResetQ, loggingGPS)
        self.gpsThread.daemon = True
        self.gpsThread.start()

    def startTempThread(self):
        self.tempThread = TempThread("tempThread", self.tempQ, self.tempExceptionsQ, self.tempResetQ, loggingTemp)
        self.tempThread.daemon = True
        self.tempThread.start()

    def checkSideThreads(self):
        """ Check to make sure the side threads are still running """
        # If either of the xbee threads need to be reset, reset them both, and recreate the xbee object just to make sure that the xbee wasn't the issue
        if(self.xbeeEnabled):
            if((not self.xReceivedResetQ.empty()) or (not self.xSendResetQ.empty())):
                try:
                    self.xbee.close()       # Try to close the xbee
                    print('xbee closed')
                except:
                    pass

                try:
                    self.xbee = serial.Serial(port = self.xPort, baudrate = self.xBaud, timeout = self.xTimeout)        # Reopen the xbee
                    self.xbee.close()
                    self.xbee.open()
                    print(self.xbee.isOpen())
                    self.startXbeeThreads()     # Restart the threads

                    # Empty the reset Qs
                    while(not self.xReceivedResetQ.empty()):
                        self.xReceivedResetQ.get()
                    while(not self.xSendResetQ.empty()):
                        self.xSendResetQ.get()
                except Exception, e:
                    print(str(e))
                    self.xbeeEnabled = False        # If this fails, disable the xbee
                    if(self.rfdEnabled):
                        self.ser.write("Alert: Xbee is now disabled")
                    print("Xbee is now Disabled")

        # If the gps thread needs to be reset, do it
        if(self.gpsEnabled):
            if(not self.gpsResetQ.empty()):
                try:
                    self.gps.close()       # Try to close the gps
                except:
                    pass

                try:
                    self.gps = serial.Serial(port = self.gpsPort, baudrate = self.gpsBaud, timeout = self.gpsTimeout)       # Reopen the GPS
                    self.startGPSThread()       # Restart the thread
                    print('here')
                    # Clear the gps reset Q
                    while(not self.gpsResetQ.empty()):
                        self.gpsResetQ.get()
                except:
                    self.gpsEnabled = False     # If this fails, disable the gps
                    if(self.rfdEnabled):
                        self.ser.write('Alert: GPS is disabled')
                    print("GPS is now Disabled")

        if(self.tempEnabled):
            if(not self.tempResetQ.empty()):
                try:
                    f = open(temp_sensor,'r')
                    lines = f.readlines()
                    f.close()
                    if(lines[0].strip()[-3:] != 'YES'):
                        self.tempEnabled = False     # If this fails, disable the gps
                        if(self.rfdEnabled):
                            self.ser.write('Alert: Temp Sensor is disabled')
                        print("Temp Sensor is Disabled")
                    else:
                        self.tempEnabled = True
                        self.tempResetQ.put('reset')
                        if(self.rfdEnabled):
                            self.ser.write('Temp Sensor is now Enabled')
                        print("Temp Sensor Enabled")
                        self.startTempThread()       # Restart the thread
                        # Clear the temp reset Q
                        while(not self.tempResetQ.empty()):
                            self.tempResetQ.get()
                except:
                    self.tempEnabled = False     # If this fails, disable the gps
                    if(self.rfdEnabled):
                        self.ser.write('Alert: Temp Sensor is disabled')
                    print("Temp Sensor is Disabled")

    def loop(self):
        """ The main loop for the program """
        try:
            ### Receive a command from the ground station and process it ###
            if(self.rfdEnabled):
                print("RT: "+ str(int(time.time() - self.starttime)) + " Watching Serial")      # Print out how long this has been running
                timeCheck = time.time()
                command = ''
                done = False
                while((not done) and (time.time() - timeCheck) < 3):
                    # Read from the RFD, if you fail to read, diabled the RFD and break the loop
                    try:
                        newChar = self.ser.read()
                    except:
                        self.rfdEnabled = False
                        done = True
                    # If the character is !, this is a command EOL character, so end the loop
                    if(newChar == "!"):
                        command += newChar
                        done = True
                    # If the character is anything else (not null), add it on, and reset the kill timer
                    elif(newChar != ""):
                        command += newChar
                        timeCheck = time.time()

                if(command != ''):
                    print("Command: ",command)

                # Check to see if the command was one for the raspberry pi
                try:
                    if(command == 'IMAGE;1!'):
                        self.mostRecentImage()
                    elif(command == 'IMAGE;2!'):
                        self.sendImageData()
                    elif(command == 'IMAGE;3!'):
                        self.requestedImage()
                    elif(command == 'IMAGE;4!'):
                        self.sendCameraSettings()
                    elif(command == 'IMAGE;5!'):
                        self.getCameraSettings()
                    elif(command == 'IMAGE;6!'):
                        self.pingTest()
                    elif(command == 'IMAGE;7!'):
                        self.sendPiRuntime()
                    elif(command == 'IMAGE;8!'):
                        self.timeSync()
                    elif(command == 'IMAGE;9!'):
                        self.horizontalFlip()
                        self.ser.reset_input_buffer()
                    elif(command == 'IMAGE;0!'):
                        self.verticalFlip()
                        self.ser.reset_input_buffer()
                    elif(command == 'IMAGE;~!'):
                        self.sendPing()
                    elif(command == 'IMAGE;-!'):
                        self.sendDeviceStatus()
                except:
                    self.rfdEnabled = False     # The only thing not exception handled in the command functions is the acknowledge send, so if this block is triggered, the RFD write failed

                # If it's not a command for the raspberry pi, send it out the xbee
                else:
                    if(self.xbeeEnabled):
                        if(command != ''):
                            self.xSendQ.put(command)      # Adds the command to the xbee send list so the xbee thread can send it out

                ### Send information to the ground station ###
                # Send a GPS update through the RFD
                if(self.gpsEnabled):
                    if(not self.gpsQ.empty()):
                        gps = "GPS:" + str(self.gpsQ.get())
                        self.ser.write(gps)
                        while(not self.gpsQ.empty()):
                            self.gpsQ.get()

                # Temperature
                if(self.tempEnabled):
                    if(not self.tempQ.empty()):
                        self.recentTemp = str(self.tempQ.get())
                        print("Temperature: "+self.recentTemp)
                        while(not self.tempQ.empty()):
                            gpsExceptionsQ
                            self.tempQ.get()
                else:
                    self.recentTemp = "False"

                # Send out everything the xbee received
                if(self.xbeeEnabled):
                    while(not self.xReceivedQ.empty()):
                        self.ser.write(self.xReceivedQ.get())

            self.checkSideThreads()             # Make sure the side threads are still going strong

            ### Periodically take a picture ###
            if(self.cameraEnabled):
                if(self.checkpoint < time.time() and not self.takingPicture):			# Take a picture periodically
                    try:
                        camera = picamera.PiCamera()
                        camera.close()
                    except:
                        self.cameraEnabled = False
                        if(self.rfdEnabled):
                            self.ser.write('Alert: Camera is disabled\n')
                        print('Camera disabled')
                    if self.cameraEnabled:
                        print("Taking Picture")
                        self.takingPicture = True
                        self.picThread = TakePicture("Picture Thread",self.cameraSettings, self.folder,self.imagenumber,self.picQ)
                        self.picThread.daemon = True
                        self.picThread.start()

            ### Check for picture stuff ###
            if(self.cameraEnabled):
                if(not self.picQ.empty()):
                    if(self.picQ.get() == 'done'):                      # Command to reset the recentimg and increment the pic number (pic successfully taken)
                        self.recentimg = "%s%04d%s" %("image",self.imagenumber,"_b.jpg")
                        self.imagenumber += 1
                        self.takingPicture = False
                        self.checkpoint = time.time() + self.pic_interval
                    elif(self.picQ.get() == 'reset'):                   # Command to reset the camera
                        self.takingPicture = False
                        self.reset_cam()
                    elif(self.picQ.get() == 'checkpoint'):              # Command to reset the checkpoint
                        self.takingPicture = False
                        self.checkpoint = time.time() + self.pic_interval
                    elif(self.picQ.get() == 'No Cam'):                  # Command to disable the camera
                        self.cameraEnabled = False
                    else:
                        while(not self.picQ.empty()):                   # Clear the queue of any unexpected messages
                            print(self.picQ.get())

            if(self.rfdEnabled):
                self.ser.reset_input_buffer()       # Clear the input buffer so we're ready for a new command to be received

            ### Print out any exceptions that the threads have experienced ###
            if(self.gpsEnabled):
                while(not self.gpsExceptionsQ.empty()):
                    print(self.gpsExceptionsQ.get())
            if(self.xbeeEnabled):
                while(not self.xReceivedExceptionsQ.empty()):
                    print(self.xReceivedExceptionsQ.get())
                while(not self.xSendExceptionsQ.empty()):
                    print(self.xSendExceptionsQ.get())
            if(self.tempEnabled):
                while(not self.tempExceptionsQ.empty()):
                    print(self.tempExceptionsQ.get())

            ### Check if there is now an Xbee, GPS, RFD, or Camera attached; if there is, get it going

            # Camera Check
            if(not self.cameraEnabled):
                try:
                    camera = picamera.PiCamera()
                    camera.close()
                    self.cameraEnabled = True
                    print('Camera is now Enabled')
                    if(self.rfdEnabled):
                        self.ser.write('Camera is now Enabled')
                except:
                    pass

            # Temp Sensor Check
            if(not self.tempEnabled):
                try:
                    f = open(temp_sensor,'r')
                    lines = f.readlines()
                    f.close()
                    if(lines[0].strip()[-3:] != 'YES'):
                        pass
                    else:
                        self.tempEnabled = True
                        self.tempResetQ.put('reset')
                        if(self.rfdEnabled):
                            self.ser.write('Temp Sensor is now Enabled')
                        print("Temp Sensor Enabled")

                except Exception, e:
                    pass

            # Xbee Check
            if (not self.xbeeEnabled):
                ports = serial.tools.list_ports.comports()
                for each in ports:
                    if each.vid == 1027 and each.pid == 24597:      # The vid and pid for the xbee, start it up if they match one of the comports
                        if(not self.xbeeEnabled):
                            self.xPort = each.device
                            try:
                                self.xbee = serial.Serial(port = self.xPort, baudrate = self.xBaud, timeout = self.xTimeout)
                                self.xbeeEnabled = True
                                self.xSendResetQ.put('reset')
                                self.xReceivedResetQ.put('reset')        # This will cause the threads to be restarted in the the checkSideThreads call next loop
                                self.xbee.close()                       # Close the xbee so it can be opened again later
                                print('Xbee is now Enabled')
                                if(self.rfdEnabled):
                                    self.ser.write('Xbee is now Enabled')
                            except Exception, e:
                                pass

            # RFD and GPS Check
            if (not self.gpsEnabled) or (not self.rfdEnabled):
                ports = serial.tools.list_ports.comports()
                for each in ports:
                    if each.vid == 1659 and each.pid == 8963:
                        if each.device != self.rfdPort:
                            gpsTest = serial.Serial(port = each.device, baudrate = 9600, timeout = 1)
                            try:
                                sample = gpsTest.readline()
                                sample = gpsTest.readline()     # Get 2 lines to make sure it's a full line
                                if sample[0:2] == "$G":
                                    self.gpsPort = each.device
                                    self.gps = serial.Serial(port = self.gpsPort, baudrate = self.gpsBaud, timeout = self.gpsTimeout)
                                    self.gpsEnabled = True
                                    self.gpsResetQ.put('reset')             # This will cause the thread to be restarted in the the checkSideThreads call next loop
                                    self.gps.close()                        # Close the GPS so it can be opened again later
                                    print('GPS is now Enabled')
                                    if(self.rfdEnabled):
                                        self.ser.write('GPS is now Enabled')
                                else:
                                    if (not self.rfdEnabled):
                                        self.rfdPort = each.device
                                        print('RFD is now Enabled')
                            except Exception, e:
                                print(str(e))

        except KeyboardInterrupt:       # For debugging pruposes, close the RFD port and quit if you get a keyboard interrupt
            self.ser.close()
            quit()

        except Exception, e:            # Print any exceptions from the main loop
            print(str(e))

if __name__ == "__main__":
    ### Check for, and create the folder for this flight ###
    folder = "/home/pi/RFD_Pi_Code/%s/" % strftime("%m%d%Y_%H%M%S")
    dir = os.path.dirname(folder)
    if(not os.path.exists(dir)):
        os.mkdir(dir)

    ### Create the logfile ###
    try:
        logfile = open(folder+"piruntimedata.txt","w")
        logfile.close()
        logfile = open(folder+"piruntimedata.txt","a")
        loggingRuntime = True
    except:
        loggingRuntime = False
        print("Failed to create piruntimedata.txt")

    sys.stdout = Unbuffered(sys.stdout)         # All print statements are written to the logfile

    try:
        gpsLog = open(folder+"gpslog.txt","a")
        gpsLog.close()
        loggingGPS = True
    except:
        loggingGPS = False
        print("Failed to create gpslog.txt")

    try:
        tempLog = open(folder+"templog.txt","a")
        tempLog.close()
        loggingTemp = True
    except:
        loggingTemp = False
        print("Failed to create templog.txt")

    mainLoop = main()
    while True:
        mainLoop.loop()
