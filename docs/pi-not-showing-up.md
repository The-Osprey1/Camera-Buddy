# The PC doesn't see the Pi

Use this when the PC helper keeps waiting and the Pi never turns up as a COM
port. Work down the steps in order and stop at the first one that finds the
problem. Most of them take a minute and need nothing but your eyes.

## 1. Check the cable goes to the right place

The PC only sees the Pi through the Pi's **USB-C port** (the power socket),
connected to a USB port on the PC.

- If the Pi now runs from its own wall power supply, the PC can't see it at
  all. That's fine only with a USB-C power and data splitter: wall power into
  the splitter's power end, the splitter's data end to the PC, and the
  splitter's single plug into the Pi.
- Use the cable that worked before. Many USB-C cables only carry power: the Pi
  lights up but the PC sees nothing.
- Try a USB port on the back of the PC, not a hub or a front-panel port.

## 2. Look at the Pi's two lights

| What you see | What it means | What to do |
| --- | --- | --- |
| No lights at all | No power reaches the Pi | Check the cable and port. Try another port. |
| Red light off or flickering | Not enough power | Use a stronger port, or a wall supply with a splitter. |
| Red steady, green flickers now and then | The Pi is running | Go to step 3. |
| Red steady, green blinks the same pattern over and over | The Pi can't start from its card | Go to step 6. |
| Red steady, green never lights | The Pi can't read its card at all | Go to step 6. |

## 3. Look at the small screen (if it's wired up)

If the screen shows **STARTING**, **SEATED**, **NO PERSON** or similar, the Pi
program is running, so the problem is only the link to the PC. Go to step 4.

If the screen stays blank but the Pi is running, that is a screen problem, not
the reason the PC can't see the Pi. Leave it for later.

## 4. Look on the PC

1. Unplug the Pi's cable from the PC, wait five seconds, plug it back in, and
   wait a full minute (the Pi needs that long to start).
2. Right-click the Start button and open **Device Manager**.
3. Open **Ports (COM & LPT)**. A new **USB Serial Device (COM...)** means the
   PC sees the Pi. The helper finds it by itself within a few seconds.
4. If instead you see **Unknown USB Device (Device Descriptor Request
   Failed)**, the Pi has power but its keyboard mode didn't start. Go to step 5.
5. If nothing new appears at all, the cable or port is the problem (step 1),
   or the Pi isn't running (step 2).

## 5. Ask the Pi directly

Open PowerShell on the PC and type the line you used before:

```
ssh theosprey@pi4.local
```

If it asks for your password, the Pi is running. Paste these lines one at a
time and note what each says:

```
cat /sys/class/udc/*/state
systemctl status desk-presence-gadget --no-pager
grep dwc2 /boot/firmware/config.txt
vcgencmd get_throttled
```

- The first line should say `configured`. `not attached` means the PC end of
  the cable isn't connected (step 1). An error means keyboard mode is off.
- The second should say `active (exited)`. `failed` means keyboard mode didn't
  start on this boot.
- The third should print `dtoverlay=dwc2,dr_mode=peripheral`. If it prints
  nothing, keyboard mode was switched off.
- The fourth should say `throttled=0x0`. Anything else means the power dipped
  at some point.

Then install the newest version, which fixes keyboard mode not starting after
a power cut, and restart:

```
sudo /opt/desk-presence/setup/update.sh main && sudo reboot
```

After a minute, type `ssh theosprey@pi4.local` again and then `desk-check`.

If ssh says it can't find `pi4.local`, the Pi either isn't running or isn't on
the Wi-Fi. Go to step 6.

## 6. Check the card

Sudden power cuts (for example when the PC shuts down and takes the Pi's
power with it) can damage the files on the card.

1. Unplug the Pi and take out its card.
2. Put the card in the PC's card reader. A drive called **bootfs** should
   appear.
3. If Windows asks to format the card, click **Cancel**. The card is damaged:
   write it again with Raspberry Pi Imager and follow "No screen or keyboard?"
   in the README. Your face sign-up is lost and needs doing again with
   `desk-face`; everything else sets itself up.
4. If **bootfs** opens and has files in it, open `desk-presence-setup.log` to
   check the first setup finished ("All done"), then put the card back and try
   step 7.

## 7. Rule out the small screen's wires

A screen wire on the wrong pin can stop the Pi from starting.

1. Unplug the Pi.
2. Pull the screen's four wires off the Pi's pins.
3. Plug the Pi back into the PC and wait a minute.

If the PC now sees the Pi, check the wires against [wiring.md](wiring.md)
before putting them back: power on pin 1 (3.3 V, never 5 V), ground on pin 6,
data on pin 3, clock on pin 5.

## Stopping it happening again

- A USB-C power and data splitter with the Pi's own wall supply keeps the Pi on
  when the PC shuts down, so it never loses power suddenly. This is the best
  protection for the card.
- The Pi now keeps its logs across restarts. If it goes missing again,
  `journalctl -b -1 -n 100` over ssh shows what happened on the start before.
