#!/bin/sh

# #############################################################################
# USER-DEFINED VARIABLES
DEVICE="PW2"                 # [PW2|PW3] Different devices require different logic; only PW2 is supported right now
SLEEP_HOURS=3                # Time to sleep between refreshes
BATTERY_ALERT_THRESHOLD=10   # Threshold after which battery value will be printed on screen
LOW_BATTERY_SLEEP_HOURS=$((5*24)) # Deep sleep when battery has reached the alert threshold
# #############################################################################


# #############################################################################
# SCRIPT VARIABLES
SCRIPT_DIR=$(pwd)
LOG_FILENAME='gphotos.log'
LOG_PATH="${SCRIPT_DIR}/${LOG_FILENAME}"
FONT="regular=/usr/java/lib/fonts/Palatino-Regular.ttf"
# #############################################################################


# #############################################################################
# PATHS TO EXECUTABLES
fbink_cmd="fbink -q"
if [ "$DEVICE" = "PW3" ]; then
    framebuf_rotate_cmd="/sys/devices/platform/imx_epdc_fb/graphics/fb0/rotate"
    backlight_cmd="/sys/devices/platform/imx-i2c.0/i2c-0/0-003c/max77696-bl.0/backlight/max77696-bl/brightness"
    rtc_device='/dev/rtc0'
elif [ "$DEVICE" = "PW2" ]; then
    framebuf_rotate_cmd="/sys/devices/platform/mxc_epdc_fb/graphics/fb0/rotate"
    backlight_cmd="/sys/devices/system/fl_tps6116x/fl_tps6116x0/fl_intensity"
    rtc_device='/dev/rtc0'
else
    echo "Unknown device: $DEVICE"
    exit 1
fi
# #############################################################################


wait_wlan_connected() {
    return "$(lipc-get-prop com.lab126.wifid cmState | grep CONNECTED | wc -l)"
}

wait_wlan_ready() {
    return "$(lipc-get-prop com.lab126.wifid cmState | grep -e READY -e PENDING -e CONNECTED | wc -l)"
}

log_info() {
    echo "$(date '+%Y-%m-%d_%H:%M:%S'): INFO : $1"
    echo "$(date '+%Y-%m-%d_%H:%M:%S'): INFO : ${1}" >> "${LOG_PATH}"
}

log_error() {
    echo "$(date '+%Y-%m-%d_%H:%M:%S'): ERROR : $1" 1>&2
    echo "$(date '+%Y-%m-%d_%H:%M:%S'): ERROR : ${1}" >> "${LOG_PATH}"
}

shave_processes() {
    log_info "Stopping most Kindle processes"
    stop lab126_gui
    ### give an update to the outside world...
    echo 0 > $framebuf_rotate_cmd
    $fbink_cmd -w -c -f -m -t $FONT,size=20,top=410,bottom=0,left=0,right=0 "Starting gphotos..." > /dev/null 2>&1
    sleep 1
    stop otaupd
    stop phd
    stop tmd
    stop x
    stop todo
    stop mcsd
    stop archive
    stop dynconfig
    stop dpmd
    stop appmgrd
    stop stackdumpd
    #stop powerd  ### otherwise the pw3 is not going to suspend to RAM?
    sleep 2
    # At this point we should be left with a more or less Amazon-free environment
    # I leave
    # - powerd & deviced
    # - lipc-daemon
    # - rcm
    # running.
}


# #############################################################################
# Main

# Dim Backlight
echo -n 0 > $backlight_cmd

log_info "------------------------------------------------------------------------"
shave_processes

### FIXME: If we have a wan module installed...
#if [ -f /usr/sbin/wancontrol ]
#then
#    wancontrol wanoffkill
#fi

log_info "Entering main loop..."
while true; do
    ### Dim Backlight
    echo -n 0 > $backlight_cmd

    ### Disable Screensaver
    lipc-set-prop com.lab126.powerd preventScreenSaver 1

    ### Disable CPU Powersave
    echo ondemand > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor

    echo 0 > $framebuf_rotate_cmd

    lipc-set-prop com.lab126.cmd wirelessEnable 1
    ### Wait for wifi interface to come up
    echo "Waiting for wifi interface to come up..."
    while wait_wlan_ready; do
        sleep 1
    done

    ### Wifi interface is up, connect to access point.
    ./wifi.sh

    ### Wait for WIFI connection
    TRYCNT=0
    NOWIFI=0
    log_info "Waiting for wifi interface to become ready..."
    while wait_wlan_connected; do
        if [ ${TRYCNT} -gt 30 ]; then
            ### waited long enough
            log_info "No Wifi... ($TRYCNT)"
            NOWIFI=1
            $fbink_cmd -x 5 "No Wifi..."
            break
        fi
      sleep 1
      TRYCNT=$((TRYCNT+1))
    done

    log_info "WIFI connected!"

    log_info "Getting new image..."
    battery_level=$(gasgauge-info -s)
    $fbink_cmd -x 20 "Getting new image..."
    if ./get_gphoto.py; then
        log_info "Python script finished"
    else
        log_error "Python script failed! Exit status: $?"
    fi
        
    # TODO: rotate accordingly
    if [ -f "photo.jpg.png" ]; then
        log_info "Found PNG"
        fbink -q -c -f -i photo.jpg.png -g w=-1,h=-1,dither=PASSTHROUGH
    else
        log_info "Found JPG"
        fbink -q -c -f -i photo.jpg -g w=-1,h=-1,dither=PASSTHROUGH
    fi

    log_info "Battery level: ${battery_level}%"

    ### Enable powersave
    echo powersave > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor

    ### flight mode on...
    lipc-set-prop com.lab126.cmd wirelessEnable 0

    sleep 2

    ### set wake up time
    if [ ${battery_level} -le ${BATTERY_ALERT_THRESHOLD} ]; then
        log_error "Battery level low!(${battery_level}%)"
        fbink -q "LOW BATTERY ${battery_level}%"
        sleep_minutes=$((60*LOW_BATTERY_SLEEP_HOURS))
        sleep_seconds=$((60*sleep_minutes))
    else
        log_info "Remaining battery: ${battery_level}%"
        sleep_minutes=$((60*SLEEP_HOURS))
        sleep_seconds=$((60*sleep_minutes))
    fi
    now=$(date +%s)
    wakeup_time=$((now+sleep_seconds))
    log_info "Wake-up time set for $(date -d @${wakeup_time})"
    log_info "Sleeping now for $sleep_seconds seconds..."
    rtcwake -d ${rtc_device} -m mem -s $sleep_seconds
    ### Go into Suspend to Memory (STR)
done

