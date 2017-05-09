"""
Still Images and Command Relay for Raspberry Pi with Xbee and RFD 900+    

Author: Austin Langford, AEM
Based on work from Scott Miller, CpE, Dylan Trafford, CpE, and David Schwerr of the Montana Space Grant Consortium
Software created for use by the Minnesota Space Grant Consortium
Purpose: To communicate with a ground transceiver to receive commands, relay them through the xbee, and to send images
Additional Features: RFD 900 based Command Center and Image Reception
Creation Date: July 2016
Last Edit Date: December 1, 2016
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

##        ### Load the camera settings from the file ###
##        try:
##            f = open(self.folder+"camerasettings.txt","r")
##            width = int(f.readline())
##            height = int(f.readline())
##            sharpness = int(f.readline())
##            brightness = int(f.readline())
##            contrast = int(f.readline())
##            saturation = int(f.readline())
##            iso = int(f.readline())
##            f.close()
##            print "Camera Settings Read"
##            
##        except:
##            print "cannot open file/file does not exist"
##            self.q.put('reset')                     # Instruct the main loop to reset the camera if there's an issue with the file
##            try:
##                f.close()
##            except:
##                pass
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
        global folder, loggingGPS
        self.folder = folder

        ports = serial.tools.list_ports.comports()
        for each in ports:
            print(each.vid,each.pid, each.hwid)
            if each.vid == 1027 and each.pid == 24597:
                self.xPort = each.device
                print('Xbee on ' + str(each.device))
            elif each.vid == 1659 and each.pid == 8963:
                self.gpsPort = each.device
                print('GPS on ' + str(each.device))
            print(each)

        ### Serial Port Initializations ###
        #RFD900 Serial Variables
        self.rfdPort  = "/dev/ttyAMA0"   #"/dev/serial0"
        self.rfdBaud = 38400
        self.rfdTimeout = 3

        # GPS Serial Variables
        self.gpsPort = "/dev/gps"           # You'll need to set up your GPS so that it can be reached by this name (http://unix.stackexchange.com/questions/66901/how-to-bind-usb-device-under-a-static-name)
        self.gpsBaud = 9600
        self.gpsTimeout = 3

        # XBee Serial Variables
##        self.xPort = "/dev/xbee"            # You'll need to set up your Xbee so that it can be reached by this name (http://unix.stackexchange.com/questions/66901/how-to-bind-usb-device-under-a-static-name)
        self.xBaud = 9600
        self.xTimeout = 3
        
        # List of pi commands listened so that they can be recognized more quickly
        self.piCommands = ['1','2','3','4','5','6','7','8','9','0','~','-']
        
        fh = open(self.folder + "imagedata.txt","w")
        fh.write("")
        fh.close()

        ### Picture Variables ###
        self.wordlength = 10000
        self.imagenumber = 0
        self.recentimg = ""
        self.pic_interval = 60
        self.cameraSettings = CameraSettings(650,450,0,50,0,0,400)
        self.reset_cam()
        self.starttime = time.time()
        print "Started at @ ",datetime.datetime.now()
        self.checkpoint = time.time()
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
        self.picQ = Queue.Queue()

        ### Try to create the various serial objects; if they fail to be created, set them as disabled ###
        # RFD 900
        try:
            self.ser = serial.Serial(port = self.rfdPort,baudrate = self.rfdBaud, timeout = self.rfdTimeout)
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
            self.gps = serial.Serial(port = self.gpsPort, baudrate = self.gpsBaud, timeout = 1)
            self.gpsEnabled = True
        except:
            self.gpsEnabled = False
            if(self.rfdEnabled):
                self.ser.write('Alert: GPS is disabled\n')
            print('GPS disabled')
            
        ### Start the appropriate side threads ###
        # Xbee
        if(self.xbeeEnabled):
            self.startXbeeThreads()

        # GPS
        if(self.gpsEnabled):
            self.startGPSThread()


    def getGPSCom(self):
        return [self.gpsPort,self.gpsBaud,self.gpsTimeout]

    def getXbeeCom(self):
        return [self.xPort,self.xBaud,self.xTimeout]

    def getRFDCom(self):
        return [self.rfdPort,self.rfdBaud,self.rfdTimeout]

    def reset_cam(self):
        """ Resets the camera to the default settings """
        self.cameraSettings = CameraSettings(650,450,0,50,0,0,400)
##        f = open(self.folder + "camerasettings.txt","w")
##        f.write(str(self.cameraSettings.getWidth())+"\n")
##        f.write(str(self.cameraSettings.getHeight())+"\n")
##        f.write(str(self.cameraSettings.getSharpness())+"\n")
##        f.write(str(self.cameraSettings.getBrightness())+"\n")
##        f.write(str(self.cameraSettings.getContrast())+"\n")
##        f.write(str(self.cameraSettings.getSaturation())+"\n")
##        f.write(str(self.cameraSettings.getISO())+"\n")
##        f.close()

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
            for x in range(pos, pos+self.wordlength):
                self.ser.write(data[x])
            return
        else:                                   # If the self.wordlength is greater than the amount remaining, send everything left
            for x in range(pos, len(data)):
                self.ser.write(data[x])
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
                if(trycnt < 7):                 # There are 5 tries to get the word through, each time you fail, drop the self.wordlength by 1000
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
            self.wordlength = 10000                       # Reset the self.wordlength in case it was changed while sending
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
        """ Command 3: Sends the requested image """
        self.ser.write('A')
        try:
            print"specific photo request recieved"
            temp = self.ser.read()
            self.ser.flushInput()
            killTime = time.time() + 5
            while(temp != 'B' and time.time() < killTime):
                temp = self.ser.read()
            imagetosend = self.ser.read(15)                  # Determine which picture to send
            self.send_image(self.folder+imagetosend)
            self.wordlength = 10000
        except Exception, e:
            print str(e)

    def sendCameraSettings(self):
        """ Command 4: Sends the camera settings """
        self.ser.write('A')
        try:
            print "Attempting to send camera settings"
            settingsStr = self.cameraSettings.getSettingsString()
            self.ser.write(settingsStr+'\n')
            print "Camera Settings Sent"
            self.ser.flushInput()
        except Exception, e:
            print(str(e))
##            print "Attempting to send camera settings"
##            camFile = open(self.folder+"camerasettings.txt","r")        # Open the camera settings file
##            temp = camFile.read()
##            while(temp != ""):      # For every line in the file, send it to the RFD
##                self.ser.write(temp)
##                temp = camFile.read()
##            self.ser.write("\r")
##            camFile.close()
##            print "Camera Settings Sent"
##        except:
##            print "cannot open file/file does not exist"
##            self.reset_cam()         # If there's an issue with camerasettings.txt, reset it

    def getCameraSettings(self):
        """ Updates the camera settings """
        self.ser.write('A')
        try:
            print "Attempting to update camera settings"
            self.ser.flushInput()
            done = False
            termtime = time.time() + 10
            self.ser.flushInput()
            temp = self.ser.read()
            while temp == '5':
                self.ser.write('A')
                temp = self.ser.read()
            while not done:
                settings = self.ser.readline()
                if settings[0] == 'A':
                    settings = settings.replace('A','')
                    settingsLst = settings.split(',')
                    if len(settingsLst) == 7:
                        fail = False
                        for each in settingsLst:
                            try:
                                each = int(each)
                            except:
                                fail = True
                        if fail == False:
                            self.cameraSettings.newSettings(settingsLst)
                            done = True
                            self.ser.write('B')
                if time.time() > termtime:
                    done = True
            temp = self.ser.read()
            while temp != '':
                temp = self.ser.read()
        except Exception,e:
            print(str(e))
                    
##            f = open(self.folder+"camerasettings.txt","w")
##            temp = self.ser.read()
##            self.ser.flushInput()
##            killTime = time.time() + 5
##            while(temp != 'B' and time.time() < killTime):
##                temp = self.ser.read()
##            while(temp != ""):
##                temp = self.ser.read()
##                f.write(temp)
##                print(temp)
##            f.close()
##            self.ser.write('A')
##            print "New Camera Settings Received"
##            if(not self.takingPicture):
##                self.checkpoint = time.time()
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
            print "test"
            termtime = time.time() + 10
            pingread = self.ser.read()
            while ((pingread != 'D') &(pingread != "") & (termtime > time.time())):     # Look for the stop character D, no new info, or too much time passing
                if (pingread == '~'):       # Whenever you get the P, send one back and get ready for another
                    print "Ping Received"
                    self.ser.flushInput()
                    self.ser.write('~')
                else:                       # If you don't get the P, sne back an A instead
                    print "pingread = ",pingread
                    self.ser.flushInput()
                    self.ser.write('A')
                pingread = self.ser.read()       # Read the next character
                sys.stdin.flush()
        except:
            print "Ping Runtime Error"

    def sendPing(self):
        """ Sends a Ping when requested """
        try:
            print "test"
            termtime = time.time() + 10
            pingread = self.ser.read()
            while ((pingread != 'D') &(pingread != "") & (termtime > time.time())):     # Look for the stop character D, no new info, or too much time passing
                if (pingread == '~'):       # Whenever you get the P, send one back and get ready for another
                    print "Ping Received"
                    self.ser.flushInput()
                    self.ser.write('~')
                else:                       # If you don't get the P, sne back an A instead
                    print "pingread = ",pingread
                    self.ser.flushInput()
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
            status = 'Camera: '+str(self.cameraEnabled)+', GPS: '+str(self.gpsEnabled)+', Xbee: '+str(self.xbeeEnabled) + '\n'
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
                    # Clear the gps reset Q
                    while(not self.gpsResetQ.empty()):
                        self.gpsResetQ.get()
                except:
                    self.gpsEnabled = False     # If this fails, disable the gps
                    if(self.rfdEnabled):
                        self.ser.write('Alert: GPS is now disabled')
                    print("GPS is now Disabled")

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
                    # If the character received is the first one, and it's a recognized Pi command, exit the loop immediately
##                    if((newChar in self.piCommands) and (len(command) == 0)):
##                        command = newChar
##                        done = True
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
                        self.ser.flushInput()
                    elif(command == 'IMAGE;0!'):
                        self.verticalFlip()
                        self.ser.flushInput()
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
                self.ser.flushInput()       # Clear the input buffer so we're ready for a new command to be received
            
            ### Print out any exceptions that the threads have experienced ###
            if(self.gpsEnabled):
                while(not self.gpsExceptionsQ.empty()):
                    print(self.gpsExceptionsQ.get())
            if(self.xbeeEnabled):
                while(not self.xReceivedExceptionsQ.empty()):
                    print(self.xReceivedExceptionsQ.get())
                while(not self.xSendExceptionsQ.empty()):
                    print(self.xSendExceptionsQ.get())

            ### Check if there is now an Xbee, GPS, RFD, or Camera attached; if there is, get it going
            # RFD Check
            if(not self.rfdEnabled):
                try:
                    self.ser = serial.Serial(port = self.rfdPort,baudrate = self.rfdBaud, timeout = self.rfdTimeout)
                    self.rfdEnabled = True
                    print('RFD is now Enabled')
                    self.ser.write("Alert: RFD is now Enabled")
                except:
                    pass

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

            # Xbee and GPS Check
            if (not self.xbeeEnabled) or (not self.gpsEnabled):
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
                            
                    elif each.vid == 1659 and each.pid == 8963:
                        if(not self.gpsEnabled):
                            self.gpsPort = each.device
                            try:
                                self.gps = serial.Serial(port = self.gpsPort, baudrate = self.gpsBaud, timeout = self.gpsTimeout)
                                self.gpsEnabled = True
                                self.gpsResetQ.put('reset')             # This will cause the thread to be restarted in the the checkSideThreads call next loop
                                self.gps.close()                        # Close the GPS so it can be opened again later
                                print('GPS is now Enabled')
                                if(self.rfdEnabled):
                                    self.ser.write('GPS is now Enabled')
                            except:
                                pass

        except KeyboardInterrupt:       # For debugging pruposes, close the RFD port and quit if you get a keyboard interrupt
            self.ser.close()
            quit()
            			
        except Exception, e:            # Print any exceptions from the main loop
            print(str(e))

if __name__ == "__main__":
    ### Check for, and create the folder for this flight ###
    folder = "/home/pi/RFD_Pics/%s/" % strftime("%m%d%Y_%H%M%S")
    dir = os.path.dirname(folder)
    if not os.path.exists(dir):
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
        
    mainLoop = main()
    while True:
        mainLoop.loop()

