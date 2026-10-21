# Desk presence

Locks your Windows PC when you walk away from your desk, using a Raspberry Pi 4B and the Raspberry Pi AI Camera.

The camera runs a body pose model on its own chip (it finds your head and shoulders about 30 times a second), so the Pi only receives the results and no pictures are ever stored. The Pi plugs into the PC's USB port and acts as an ordinary keyboard: when you leave, it presses **Windows + L**. Locking keeps every program running; nothing closes.

This is step one of the bigger plan. Auto-unlock comes later.

## What's in here

| Folder | What it is |
|---|---|
| `pi/desk_presence/` | The program that runs on the Pi |
| `pi/setup/` | Install script, USB keyboard setup and start-at-boot services |
| `pi/config.example.toml` | All the settings, with explanations |
| `pc-helper/desk_helper.py` | A small optional program for the PC (pause hotkey, lock status, warnings) |

## Safeguards against locking by mistake

- **You have to actually leave.** Standing up, stretching, leaning back or turning around never locks. Your body has to leave the camera's view and stay gone for 5 seconds (`away_seconds`).
- **No flicker locks.** A body has to be seen for half a second before it counts as back, and a single missed frame doesn't count as gone.
- **It has to have seen you first.** It only locks after it has seen you sitting there, so switching the Pi on in an empty room does nothing.
- **One lock per absence**, and nothing at all if the PC helper says the PC is already locked.
- **Too dark to judge means do nothing.** It won't lock you out while you're sitting in the dark.
- **A second person only causes a warning** (a beep on the PC) by default. You can change it to lock, or turn it off.
- **Pause switch:** **Ctrl + Alt + P** on the PC (with the helper running), or an optional button wired to the Pi, pauses everything for an hour. Press it again to resume. Unplugging the Pi also stops it instantly.
- **Practice mode is on by default.** It writes down what it *would* have done without pressing any keys. Run it like this for a week, read the log, tune the timings, then switch it off.

And if it does lock by mistake: it's just Windows + L. Type your PIN as usual.

## What you need

- Raspberry Pi 4B and the Raspberry Pi AI Camera
- A **USB-C power and data splitter**. A PC's USB port may not power a Pi 4 plus camera reliably (`desk-check` tells you if the power dips), so a proper 5V/3A supply powers the Pi and only the data line goes to the PC.
- A Windows PC. On the PC, set sleep to "Never" when plugged in (Settings, System, Power), because sleep pauses your programs and locking doesn't.

## Setting up the Pi

1. Install Raspberry Pi OS (Bookworm or newer) and connect the AI Camera.
2. Copy this repository onto the Pi, then from the `pi` folder run:

   ```
   sudo ./setup/install.sh
   sudo reboot
   ```

   This installs the camera software, turns on keyboard mode for the USB-C port, puts your settings in `~/desk-presence/config.toml` and starts the program at every boot.
3. Plug the splitter's data side into the PC. Windows should find a new keyboard and a new "USB Serial Device".
4. Check everything with one command (it presses nothing):

   ```
   desk-check
   ```

   It tells you on screen when to sit, stand up, walk away and come back, and beeps when it's time for the next step. Then it gives OK or PROBLEM for the power, for how the camera saw each of those, and for keyboard mode, with what to change. The results are saved in `~/desk-presence/checks/`. The first start of the camera uploads the model to it, which can take a minute.

   When the Pi is online, `desk-check` first installs the newest version of this program from GitHub, so after a fix you just run it again. To update without checking, run `sudo /opt/desk-presence/setup/update.sh`.

   To watch the camera's raw results instead, once a second: `sudo systemctl stop desk-presence`, then `cd /opt/desk-presence && python3 -m desk_presence camera-test`. Start the program again afterwards with `sudo systemctl start desk-presence`.

### No screen or keyboard on the Pi?

The Pi can do steps 1 and 2 by itself from the SD card:

1. Write **Raspberry Pi OS Lite (64-bit)** to the card with Raspberry Pi Imager. In its settings, set the name to `deskcam`, pick a username and password, enter your Wi-Fi, and turn on SSH (remote login).
2. Put the card back into your computer. Copy the `pi` folder onto the card's boot drive and rename the copy `desk-presence`.
3. Open `user-data` on the boot drive in a text editor and add these two lines at the end, with your username in place of `yourname`. If the file already has a `runcmd:` line, add only the second line at the end of its list, indented like the lines above it.

   ```
   runcmd:
   - [bash, /boot/firmware/desk-presence/setup/firstboot.sh, yourname]
   ```

4. Put the card in the Pi and switch it on. It waits for the Wi-Fi, updates itself, installs everything and restarts, which can take half an hour. It writes its progress to `desk-presence-setup.log` on the boot drive.
5. From the PC, log in with `ssh yourname@deskcam.local` and carry on from step 3.

## Setting up the PC helper (optional, recommended)

Locking works without it. The helper adds the Ctrl + Alt + P pause switch, the second-person warning beep, and tells the Pi when the PC is already locked.

1. Install Python from python.org, then in a terminal: `py -m pip install pyserial`
2. Copy the `pc-helper` folder somewhere on the PC.
3. Try it: `py desk_helper.py`. It should say "Connected to the Pi on COM...".
4. To start it at every login without a window: press Windows + R, type `shell:startup`, and in that folder create a shortcut to `pyw C:\path\to\desk_helper.py`.

Its log is in `%LOCALAPPDATA%\DeskPresence\helper.log`.

## Practice week, then going live

Each day, check what it would have done:

```
cd /opt/desk-presence && python3 -m desk_presence --config ~/desk-presence/config.toml log
```

Every `would_lock` line should match a time you really walked away. If it locked too eagerly, raise `away_seconds`. If it missed you leaving, check `camera-test` and the camera angle. When you're happy, set `practice_mode = false` in `~/desk-presence/config.toml` and run `sudo systemctl restart desk-presence`.

## Messages on the cable

The Pi and the helper only ever send these lines, and ignore anything else:

- PC to Pi: `LOCKED`, `UNLOCKED` (every 2 seconds), `PAUSE` (toggle)
- Pi to PC: `PAUSED`, `RESUMED`, `WARN SECOND_PERSON`, `LOCKING`, `WOULD_LOCK`

## Running the tests

```
pip install pytest
python -m pytest pi/tests pc-helper/tests
```

The tests don't need a Pi or a camera.

## Not done yet

- Auto-unlock (face recognition, typing the PIN, the anti-spoofing layers)
- Extras: pausing music when you leave, posture and break reminders, desk time report
