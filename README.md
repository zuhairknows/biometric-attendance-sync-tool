# Biometric Attendance Sync Tool <span style="font-size: 0.6em; font-style: italic">(For ERPNext)</span>

Python Scripts to poll your biometric attendance system _(BAS)_ for logs and sync with your ERPNext instance


## Table of Contents
 - [Pre-requisites](#pre-requisites)
 - [Usage](#usage)
    - [GUI](#gui)
    - [CLI](#cli)
 - [Setup Specifications](#setup-specifications-(for-cli))
    - [UNIX](#unix)
    - [Windows](#windows)
  - [Setting Up Config](#setting-up-config)
  - [Resources](#resources)
  - [License](#license)


## Pre-requisites
* Python 3.10+ recommended for current dependencies on Windows.


## Usage
There's two ways you can use this tool. If accessing the CLI is a bit of a pain for you, the GUI has a simple form to guide you through the process.

Under [/releases](https://github.com/frappe/biometric-attendance-sync-tool/releases), for a particular release download the `biometric-attendance-sync-tool-[version]-[distribution].zip` and unzip it's contents. Now from the location of the unzipped files, you can go ahead with the CLI or GUI method.

### GUI
Run the `attendance-sync` file from the folder; This should setup all it's dependencies automatically and start the GUI.

### CLI
The `erpnext_sync.py` file is the "backbone" of this project. Apart from Windows _(which has its own wrapper `erpnext_sync_win.py`)_, this file can be directly used to set up the sync tool. Further information provided in the [/Wiki](https://github.com/frappe/biometric-attendance-sync-tool/wiki).


## Setup Specifications (For CLI)

1. Setup dependencies
    ```
    cd biometric-attendance-sync-tool
      && python3 -m venv venv
      && source venv/bin/activate
      && pip install -r requirements.txt
    ```
2. Setup `local_config.py`

   Make a copy of and rename `local_config.py.template` file. [Learn More](#setting-up-config)

3. Run this script using `python3 erpnext_sync.py`

### UNIX

There's a [Wiki](https://github.com/frappe/biometric-attendance-sync-tool/wiki/Running-this-script-in-production) for this.

### Windows

Installing as a Windows service

1. Install dependencies using `pip install -r requirements.txt`
2. Go to this repository's Directory
3. Install the windows service using `python erpnext_sync_win.py install`
4. Done

#### Update the installed windows service
    python erpnext_sync_win.py update

#### Stop the windows service
    net stop ERPNextBiometricPushService

#### To see the status of the service
    mmc Services.msc


## Setting up config
- You need to make a copy of `local_config.py.template` file and rename it to `local_config.py`
- Please fill in the relevant sections in this file as per the comments in it.
- Below are the delineation of the keys contained in `local_config.py`:
  - ERPNext connection configs:
    - `ERPNEXT_API_KEY`: The API Key of the ERPNext User
    - `ERPNEXT_API_SECRET`: The API Secret of the ERPNext User

      > Please refer to [this link](https://frappe.io/docs/user/en/guides/integration/how_to_set_up_token_based_auth#generate-a-token) to learn how to generate API key and secret for a user in ERPNext.
      > The ERPNext User who's API key and secret is used, needs to have at least the following permissions:
      > 1. Create Permissions for 'Employee Checkin' DocType.
      > 2. Write Permissions for 'Shift Type' DocType.

    - `ERPNEXT_URL`: The web address at which you would access your ERPNext. eg:`'https://yourcompany.erpnext.com'`, `'https://erp.yourcompany.com'`
    - `ERPNEXT_VERSION`: The base version of your ERPNext/HRMS app. eg: 13, 14, 15
  - This script's operational configs:
    - `PULL_FREQUENCY`: The time in minutes after which a pull for punches from the biometric device and push to ERPNext is attempted again.
    - `LOGS_DIRECTORY`: The Directory in which the logs related to this script's whereabouts are stored.
      > Hint: For most cases you can leave the above two keys unchanged.
    - `IMPORT_START_DATE`: The date after which the punches are pushed to ERPNext. Expected Format: `YYYYMMDD`.
      > For some cases you would have a lot of old punches in the biometric device. But, you would want to only import punches after certain date. You could set this key appropriately. Also, you can leave this as `None` if this case does not apply to you.
    - `ERPNEXT_REQUEST_TIMEOUT`: The maximum number of seconds to wait for ERPNext API responses.
  - Biometric device configs:
    - `device_id`: Permanent unique device identifier. This is sent to ERPNext Employee Checkin and is also used for local retry/status identity. Do not change it when only the IP address or port changes.
    - `ip`: Device IP address or hostname. `host` is also accepted by the sync engine.
    - `port`: Optional ZKTeco TCP port. Defaults to `4370` when omitted.
    - `password`: Optional ZKTeco connection password. Defaults to `0` when omitted.
    - `punch_direction`: `'IN'`, `'OUT'`, `'AUTO'`, or `None`.
    - `clear_from_device_on_fetch`: Keep this `False` for production. Setting it to `True` can delete attendance records from the biometric device after fetch.
    - `latitude` / `longitude`: Optional. Required only when HRMS geolocation tracking requires them.

Example device:

```
devices = [
    {
        'device_id': 'FP1_DEVICE_01',
        'ip': '10.0.0.20',
        'port': 4371,
        'password': 0,
        'punch_direction': None,
        'clear_from_device_on_fetch': False
    }
]
```

For ERPNext/HRMS v15, ZKTeco User ID/PIN should match the Employee Attendance Device ID.

> TODO: fill this section with more info to help Non-Technical Individuals.

## FPF Production Deployment

This fork is prepared for the FPF ZKTeco to ERPNext attendance integration on a Windows integration PC. ERPNext/HRMS v15 remains the authoritative attendance system; this tool only polls ZKTeco devices and submits Employee Checkin records.

Production behavior:
- ZKTeco User ID/PIN must match the ERPNext Employee `attendance_device_id`.
- Each biometric device must have a permanent logical `device_id`, such as `FP1_DEVICE_01`. Do not change `device_id` when only the device IP address, hostname, port, or password changes.
- Device IP/host, port, and connection password are network settings and can be updated independently from `device_id`.
- Retry dump files, success logs, failure logs, and status keys use `device_id` for local identity.
- Known duplicate Employee Checkin responses from ERPNext are treated as idempotent/already synchronized when ERPNext returns HTTP 417 with the expected duplicate timestamp message. Other HTTP 417 responses remain failures.
- Logs use UTF-8 so Arabic Shift Type names and mixed Arabic/English messages can be written on Windows.
- `local_config.py` contains production credentials and must remain local and untracked by Git.

Do not enable `clear_from_device_on_fetch` in production.

The Windows service wakes on a short heartbeat so it can stop promptly, but `PULL_FREQUENCY` in `local_config.py` controls the actual biometric synchronization frequency.

See [Windows service deployment](docs/windows-service.md) and [production checklist](docs/production-checklist.md) before installing the service.

## FPF Biometric Sync Manager

This branch includes a Windows desktop utility named `FPF Biometric Sync Manager` for non-technical operation of the existing Windows service.

It can show service health, validate configuration, test ERPNext, test ZKTeco devices, run one manual sync cycle, and open the logs/config folders. It does not replace the sync engine and does not change the Windows service name.

Launch locally from this folder with:

```text
python -m manager.app
```

See [FPF Biometric Sync Manager](docs/sync-manager.md) for details.

## To build executable file for GUI
### Linux and Windows:
1. Activate virtual environment.
1. Navigate to the repository folder (where `gui.py` located) by
    ```
    cd biometric-attendance-sync-tool
    ```
1. Run the following commands:
    ```
    pip install pyinstaller
    ```

    ```
    python -m PyInstaller --name="attendance-sync" --windowed --onefile gui.py
    ```
1. The executable file `attendance-sync` created inside `dist/` folder.

### Resources

* Article on [ERPNext Documentation](https://docs.erpnext.com/docs/user/manual/en/setting-up/articles/integrating-erpnext-with-biometric-attendance-devices).
* This Repo's [/Wiki](https://github.com/frappe/biometric-attendance-sync-tool/wiki).

### License

This project is licensed under [GNU General Public License v3.0](LICENSE)
