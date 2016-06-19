import time, threading
from time import strftime
import subprocess
import datetime
import io
import picamera
import serial
import sys
import os
import Image
import base64
import hashlib
import re
import string
from array import array
import RPi.GPIO as GPIO


# -------------------------    GPIO inits  ---------------------------------------------
# ------- Raspberry Pi pin configuration: -----
# camera mux enable pins
# board numbering
#selection = 7                           # variable used for GPIO pin 7  - mux "selection"
#enable1 = 11                            # variable used for GPIO pin 11 - mux "enable 1"
#enable2 = 12                            # variable used for GPIO pin 12 - mux "enable 2"

# broadcom numbering    ***** used by adafruits libraries *****
#  **** may have to remove GPIO settings if using a mux or other "shield"
selection = 4
enable1 = 17
enable2 = 18

# GPIO.setmode(GPIO.BOARD)        # use board numbering for GPIO header vs broadcom **** broadcom used in adafruit library dependant stuff ****
GPIO.setmode(GPIO.BCM)           # broadcom numbering, may not matter if not using oled or any adafruit libraries that need BCM
# GPIO settings for camera mux
GPIO.setup(selection, GPIO.OUT)         # mux "select"
GPIO.setup(enable1, GPIO.OUT)           # mux "enable1"
GPIO.setup(enable2, GPIO.OUT)           # mux "enable2"
# -------------------------------------------------------------------------------------------


#  ---------------------  Comms inits ----------------------
#Serial Variables
port  = "/dev/ttyAMA0"
baud = 38400
timeout = 5
wordlength = 10000
checkOK = ''
ser = serial.Serial(port = port, baudrate = baud, timeout = timeout)
#  ----------------------------------------------------------

#  -------------------  camera and directory initis  -----------------
pic_interval = 60
extension = ".jpg"
#  **** folder can be machine specific  ****
folder = "/home/pi/RFD_Pi_Code/%s/" % strftime("%m%d%Y_%H%M%S")

dir = os.path.dirname(folder)
if not os.path.exists(dir):
    os.mkdir(dir)
    
fh = open(folder + "imagedata.txt","w")
fh.write("")
fh.close()

class Unbuffered:
    def __init__(self,stream):
        self.stream = stream
    def write(self,data):
        self.stream.write(data)
        self.stream.flush()
        logfile.write(data)
        logfile.flush()

logfile = open(folder+"piruntimedata.txt","w")
logfile.close()
logfile = open(folder+"piruntimedata.txt","a")
sys.stdout = Unbuffered(sys.stdout)


###########################
# Initial Camera Settings #
###########################
imagenumber = 0
recentimg = ""
#Camera Settings
width = 650
height = 450 
resolution = (width,height)
sharpness = 0
brightness = 50
contrast = 0
saturation = 0
iso = 100
camera_annotation = ''                # global variable for camera annottation, initialize to something to prevent dynamic typing from changing type
cam_hflip = True                       # global variable for camera horizontal flip
cam_vflip = True                       # global variable for camera vertical flip

#  ------------------------------------- end of opening inits  -------------------


#  ------------------------  Method/funciton defs  -------------------------------

###############################
# Cameras B-D are used in the #
# multiplexer system. In the  #
# single camera system they   #
# are never used.             #
###############################

def enable_camera_A():
    global cam_hflip
    global cam_vflip
    global camera_annotation
    GPIO.output(selection, False)
    GPIO.output(enable2, True)          # pin that needs to be high set first to avoid enable 1 and 2 being low at same time
    GPIO.output(enable1, False)         # if coming from a camera that had enable 2 low then we set enable 1 low on next camera
    #GPIO.output(enable2, True)         # first, we would have both enables low at the same time
    cam_hflip = True
    cam_vflip = True
    camera_annotation = ''
    time.sleep(0.5)
    return

def enable_camera_B():
    global cam_hflip
    global cam_vflip
    global camera_annotation
    GPIO.output(selection, True)
    GPIO.output(enable2, True)
    GPIO.output(enable1, False)
    #GPIO.output(enable2, True)
    cam_hflip = True
    cam_vflip = True
    camera_annotation = ''
    time.sleep(0.5)                        # ??? are these delays going to mess with timming else where ???
    return

'''
def enable_camera_C():
    global cam_hflip
    global cam_vflip
    global camera_annotation
    GPIO.output(selection, False)
    GPIO.output(enable1, True)           # make sure first enable pin to be changed is going high
    GPIO.output(enable2, False)
    cam_hflip = False
    cam_vflip = False
    camera_annotation = 'Camera C'
    time.sleep(0.5)
    return

def enable_camera_D():
    global cam_hflip
    global cam_vflip
    global camera_annotation
    GPIO.output(selection, True)
    GPIO.output(enable1, True)
    GPIO.output(enable2, False)
    cam_hflip = False
    cam_vflip = False
    camera_annotation = 'Camera D'
    time.sleep(0.5)
    return
'''

# method to decrease packet size
def decrease_wordlength():
    global wordlength
    wordlength -= 1000      # *** maybe make this increment/decrement a variable
    print 'wordlength set to : ', wordlength

# method to increae packet size
def increase_wordlength():
    global wordlength
    wordlength += 1000      # *** maybe make this increment/decrement a variable
    print 'wordlength set to : ', wordlength

###########################
# Method is used to reset #
# the camera to default   #
# settings.               #
###########################
   
def reset_cam():
    global width
    global height
    global sharpness
    global brightness
    global contrast
    global saturation
    global iso
    width = 650
    height = 450 
    resolution = (width,height)
    sharpness = 0
    brightness = 50
    contrast = 0
    saturation = 0
    iso = 100
    file = open(folder + "camerasettings.txt","w")
    file.write(str(width)+"\n")
    file.write(str(height)+"\n")
    file.write(str(sharpness)+"\n")
    file.write(str(brightness)+"\n")
    file.write(str(contrast)+"\n")
    file.write(str(saturation)+"\n")
    file.write(str(iso)+"\n")
    file.close()


# Converts the image to an array of data points
def image_to_b64(path):
    with open(path,"rb") as imageFile:
        return base64.b64encode(imageFile.read())

# Converts an array of data points into an image
def b64_to_image(data,savepath):
    fl = open(savepath,"wb")
    fl.write(data.decode('base4'))
    fl.close()

# Generates the checksum used to verify packet transmission
def gen_checksum(data,pos):
    return hashlib.md5(data[pos:pos+wordlength]).hexdigest()

# Verifies the checksums
def sendword(data,pos):
    if(pos + wordlength < len(data)):
        for x in range(pos, pos+wordlength):
            ser.write(data[x])
        return
    else:
        for x in range(pos, len(data)):
            ser.write(data[x])
        return
################################################################
# Sync is used to sync the groundstation and the image system. #
# It prevents an infinite loop by checking 5 times             #
################################################################    
def sync():
    synccheck = ''
    synctry = 5
    syncterm = time.time() + 10
    while((synccheck != 'S')&(syncterm > time.time())):
        ser.write("sync")
        synccheck = ser.read()
        if(synctry == 0):
            if (synccheck == ""):
                print "SyncError"
                break
        synctry -= 1
    time.sleep(0.5)
    return

# Transmits the image and uses the checksum method to verify transmission
#  *** Why are we passing worlength and not using global wordlength here ???  ***
def send_image(exportpath, wordlength):   # *** why not using global wordlength ??? ***
    timecheck = time.time()
    done = False
    cur = 0
    trycnt = 0
    outbound = image_to_b64(exportpath)
    size = len(outbound)
    print size,": Image Size"
    print "photo request received"
    while(cur < len(outbound)):
        print "Send Position:", cur," // Remaining:", int((size - cur)/1024), "kB"
        checkours = gen_checksum(outbound,cur)
        ser.write(checkours)
        sendword(outbound,cur)
        checkOK = ser.read()
        if (checkOK == 'Y'):
            cur = cur + wordlength
            trycnt = 0
        else:
            if(trycnt < 3):
                sync()
                trycnt += 1
                print "try number:", trycnt
                print "resending last @", cur
                print "ours:",checkours
            else:
                print "error out"
                cur = len(outbound)
    print "Image Send Complete"
    print "Send Time =", (time.time() - timecheck)
    return

#  ---------------- end of method/funciton defs  -------------------

#  --------------  Last inits  --------------------
reset_cam()
starttime = time.time()
print "Startime @ ",starttime
checkpoint = time.time()

enable_camera_A()          # initialize the camera to something so mux is not floating
                           # maybe remove enabling camera if not using mxu???
# -------  last of inits and start program loop --------


#  ------------  starting program loop  ------------------
# *** should make these methods and use a switch/case ****

#  **  maybe make RFD library to import, then swtich/case and program loop 
#  **  would make easy to read and change    ****
while(True):
    print "RT:",int(time.time() - starttime),"Watching Serial"
    command = ser.read()
    if (command == '1'):
        ser.write('A')
        try:
            print "Send Image Command Received"
            #sync()
            print "Sending:", recentimg
            ser.write(recentimg)
            send_image(folder+recentimg, wordlength)
        except:
            print "Send Recent Image Error"
    if (command == '2'):
        ser.write('A')
        try:
            print "data list request recieved"
            #sync()
            file = open(folder+"imagedata.txt","r")
            print "Sending imagedata.txt"
            for line in file:
                ser.write(line)
                #print line
            file.close()
            time.sleep(1)
        except:
            print "Error with imagedata.txt read or send"
    if (command == '3'):
        ser.write('A')
        try:
            print"specific photo request recieved"
            sync()
            imagetosend = ser.read(15)
            send_image(folder+imagetosend,wordlength)
        except:
            print "Send Specific Image Error"

# *** should make these methods and use a switch/case ****
    if (command == '4'):
        ser.write('A')
        try:
            print "Attempting to send camera settings"
            #sync()
            file = open(folder+"camerasettings.txt","r")
            temp = file.read()
            while(temp != ""):
                ser.write(temp)
                temp = file.read()
            ser.write("\r")
            file.close()
            print "Camera Settings Sent"
        except:
            print "cannot open file/file does not exist"
            reset_cam()
    if (command == '5'):
        ser.write('A')
        try:
            print "Attempting to update camera settings"
            file = open(folder+"camerasettings.txt","w")
            temp = ser.read()
            while(temp != ""):
                file.write(temp)
                temp = ser.read()
            file.close()
            print "New Camera Settings Received"
            ser.write('A')
            checkpoint = time.time()
        except:
            print "Error Retrieving Camera Settings"
            reset_cam()
    if (command == '6'):
            ser.write('A')
            print "Ping Request Received"
            try:
                termtime = time.time() + 10
                pingread = ser.read()
                while ((pingread != 'D') & (pingread != "")&(termtime > time.time())):
                    if (pingread == 'P'):
                        print "Ping Received"
                        ser.flushInput()
                        ser.write('P')
                    else:
                        print "pingread = ",pingread
                        ser.flushInput()
                        ser.write('A')
                    pingread = ser.read()
                    sys.stdin.flush()
            except:
                print "Ping Runtime Error"
    if (command == '7'):
        ser.write('A')
        try:
            print "Attempting to send piruntimedata"
            #sync()
            file = open(folder+"piruntimedata.txt","r")
            temp = file.readline()
            while(temp != ""):
                ser.write(temp)
                temp = file.readline()
            #ser.write("\r")
            file.close()
            print "piruntimedata.txt sent"
        except:
            print "error sending piruntimedata.txt"

# ------  camera/mux commands  --------  modify these to easily repurpose gui buttons in ground code

    if (command == '8'):             # enable camera a
        ser.write('A')
        try:
            print 'command received to enable camera A, attempting to enable camera A'
            enable_camera_A()
            #time.sleep(2)
            print 'returned from enabling camera A'

        except:
            print 'Not done, need to implement catch condition for enable camera A'

    if (command == '9'):             # enable camera b
        ser.write('A')
        try:
            print 'command received to enable camera B, attempting to enable camera B'
            enable_camera_B()
            #time.sleep(2)
            print 'returned from enabling camera B'

        except:
            print 'Not done, need to implement catch condition for enable camera B'

## **** should we use a "a" or not confuse with ack ???
    if (command == 'b'):
        ser.write('A')
        try:
            print 'decrease_wordlength() called'
            decrease_wordlength()
            
            print 'returned from decrease_wordlength()'

        except:
            print 'Not done, need to implement catch condition for command b'
            
    

# -----  end of camera commands  -----------------

    if (command == 'T'):
        ser.write('A')
        try:
            print "Time Sync Request Recieved"
            
            timeval=str(datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S"))+"\n"
            for x in timeval:
                ser.write(x)
        except:
            print "error with time sync"
    

#Creates a loop to check when a picture needs to be taken
    if (checkpoint < time.time()):
        camera = picamera.PiCamera()
        try:
            file = open(folder+"camerasettings.txt","r")
            width = int(file.readline())
            height = int(file.readline())
            sharpness = int(file.readline())
            brightness = int(file.readline())
            contrast = int(file.readline())
            saturation = int(file.readline())
            iso = int(file.readline())
            file.close()
            print "Camera Settings Read"
        except:
            print "cannot open file/file does not exist"
            reset_cam()
        camera.sharpness = sharpness
        camera.brightness = brightness
        camera.contrast = contrast
        camera.saturation = saturation
        camera.iso = iso
        #camera.annotate_text = "Image:" + str(imagenumber)
        camera.resolution = (2592,1944)
        extension = '.png'
        camera.hflip = cam_hflip
        camera.vflip = cam_vflip
        camera.annotate_background = picamera.Color('black')
        camera.annotate_text = camera_annotation
        #camera.start_preview()
        camera.capture(folder+"%s%04d%s" %("image",imagenumber,"_a"+extension))
        print "( 2592 , 1944 ) photo saved"
        #UpdateDisplay()
        fh = open(folder+"imagedata.txt","a")
        fh.write("%s%04d%s @ time(%s) settings(w=%d,h=%d,sh=%d,b=%d,c=%d,sa=%d,i=%d)\n" % ("image",imagenumber,"_a"+extension,str(datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")),2592,1944,sharpness,brightness,contrast,saturation,iso))
        camera.resolution = (width,height)
        extension = '.jpg'
        camera.hflip = cam_hflip
        camera.vflip = cam_vflip
        camera.annotate_text = camera_annotation
        camera.capture(folder+"%s%04d%s" %("image",imagenumber,"_b"+extension))
        print "(",width,",",height,") photo saved"
        fh.write("%s%04d%s @ time(%s) settings(w=%d,h=%d,sh=%d,b=%d,c=%d,sa=%d,i=%d)\n" % ("image",imagenumber,"_b"+extension,str(datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")),width,height,sharpness,brightness,contrast,saturation,iso))
        print "settings file updated"
        #camera.stop_preview()
        camera.close()
        #print "camera closed"
        recentimg = "%s%04d%s" %("image",imagenumber,"_b"+extension)
        #print "resent image variable updated"
        fh.close()
        #print "settings file closed"
        print "Most Recent Image Saved as", recentimg
        imagenumber += 1
        checkpoint = time.time() + pic_interval
    ser.flushInput()
    ser.flushOutput()


