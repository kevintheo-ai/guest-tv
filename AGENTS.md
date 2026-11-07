# AGENTS.md

guest-tv is a free template: a hotel-style home screen for rooted LG webOS TVs in a holiday rental, plus a Raspberry Pi that greets each guest and resets the TVs after every stay. Your goal is to adapt it to the place of the person whose TVs these are, called "the owner" in this file, and install it on their TVs. You prove each step with the check named under "Done when:".

- Do the steps in order. Start the next one when the "Done when:" line of the current one is true.
- Run development commands on the owner's Mac or Linux machine and Pi commands on the Pi over SSH. Name a TV before you change anything on it.
- Rooting, power plugs and TV menus are the owner's hands. Ask, then wait for the confirmation.
- Write the app in plain HTML, CSS and JavaScript without a framework. Use only CSS that Chromium 53 (webOS 4) supports: flexbox instead of grid, flex children at full width (`align-items: stretch`). The TV browsers are old.

## 1. Ask the owner

Ask everything in one message, in the owner's language. Do not guess.

- TV models and webOS versions. Are they rootable? Stop for any TV whose firmware the Homebrew project does not list.
- Which TVs: IP address (fixed in the router) and Wi-Fi MAC address of each.
- The Pi's address and user.
- Booking system: Smoobu (apartment ID) or something else (then a bookings file shaped like test/fixtures/bookings.json).
- Check-out and check-in times.
- Languages.
- Property name for the welcome strings.
- Guest Wi-Fi name and password (guest network only).
- Location for the weather, and the timezone.
- Mountain points for the weather.
- Bus stop (Switzerland only).
- Webcams, with permission to show them.
- Local tips.
- Emergency numbers for the country.
- Contact and WhatsApp.
- Guest card and welcome video links.
- The live TV app and the streaming apps on the TVs.
- Which accounts stay logged in.
- Whether the reset after each departure should run.

Done when: you show the owner a list with an answer to every question, or "not needed", and the owner confirms it.

## 2. Get the template, run the baseline, adapt it

Run the tests on the unmodified clone first:

```
git clone https://github.com/kevintheo-ai/guest-tv && cd guest-tv
node test/selfcheck.mjs
python3 test/test_state_builder.py
python3 test/test_tv_settings.py
python3 test/test_turnover.py
python3 test/test_scripts.py
pip install playwright==1.58.0
python3 -m playwright install chromium
python3 test/ui_walkthrough.py
```

test_scripts.py checks the TV scripts offline with stand-ins for the TV's tools: install result, hotel mode way back, SSH only with the key.

Then create the config (git ignores pi/config.json):

```
cp pi/config.example.json pi/config.json
```

Fill it from the answers; pi/config.py documents every field. Then adapt the rest:

- app/config.js takes the Pi's address and the app IDs of the streaming apps.
- The six files in app/i18n/ take the property name and the welcome texts (keys like greet.vacant and vacant.title).
- pi/content/content.json holds local tips, house rules, help and frequent questions, each in all six languages. Replace the other sample items (emergency numbers, contact, guest card and welcome video links) where the sample content keeps them.
- Check-out 11:00 and check-in 16:00 are constants in pi/state_builder.py, pi/turnover.py and app/js/app.js, plus texts in app/i18n/. Change all of them if the owner's times differ.
- The app ID com.example.guesttv appears in several files. Rename it in all of them for the owner's build.
- Optional own screensaver photos go to app/assets/img/.

Done when: the six test commands (selfcheck, four Python tests, walkthrough with all 230 checks) succeed on the unmodified clone and again after your changes (selfcheck fails if the app IDs differ), and no guest-visible text still comes from the sample content ("Village Bakery" style).

## 3. Build the package and set up the Pi

```
sh build/build_ipk.sh
scp -r pi <user>@<pi-ip>:~/guest-tv-setup
ssh -t <user>@<pi-ip> 'cd ~/guest-tv-setup && sudo bash bootstrap.sh'
scp build/dist/com.example.guesttv_1.0.0_all.ipk <user>@<pi-ip>:
```

The build needs tar, ar, python3 and qrencode, no LG SDK. The Pi runs as a normal user; code and state live in ~/guest-tv. If you renamed the app ID, the .ipk name in the last command changes with it. Copying pi/ also brings pi/config.json to the Pi. bootstrap.sh installs it as ~/guest-tv/config.json when there is none yet, or the example if pi/config.json is missing. Later changes go directly into ~/guest-tv/config.json on the Pi; after a change of the TV list run `sudo systemctl restart guest-tv.service`. bootstrap.sh asks once for the Smoobu keys and the guest Wi-Fi and stores them in /etc/guest-tv/secrets.env and /etc/guest-tv/wlan.env, readable only by root and that user's group. An empty answer saves nothing, so the next run asks again. The Wi-Fi name and password are stored exactly as typed, spaces included.

Done when: the .ipk exists in build/dist/, bootstrap.sh exits without error, the .ipk is on the Pi, ~/guest-tv/config.json there holds the owner's values and not the example's, and /etc/guest-tv/secrets.env (if Smoobu is used) and /etc/guest-tv/wlan.env exist on the Pi, and `ls -l` shows them readable only by root and the Pi user's group.

## 4. Prepare each TV

Rooting is the owner's job (see guardrails). Go on when the TV runs the Homebrew Channel. Then, with the owner:

- Switch off automatic updates on the TV.
- Generate a key on the Pi and put the public key into /home/root/.ssh/authorized_keys on the TV.
- In the Homebrew Channel enable SSH, disable telnet and block LG's update servers. These write the flag files /var/luna/preferences/webosbrew_sshd_enabled, webosbrew_telnet_disabled and webosbrew_block_updates. Then cold boot the TV (power plug).
- Fix the TV's IP address in the router and note its Wi-Fi MAC address.
- Switch Quick Start+ on and make sure Wake-on-LAN over Wi-Fi is on (network.wolwowlOnOff = true).

```
ssh-keygen -t ed25519
ssh -i ~/.ssh/id_ed25519 root@<tv-ip>
luna-send -i luna://com.webos.settingsservice/setSystemSettings '{"category":"option","settings":{"quickStartMode":"on"}}'
```

The first two commands run on the Pi, the third inside the root shell on the TV. Read its reply as described under "Commands on the TV". After each cold boot the TV has a new SSH host key, so remove its old known_hosts entry when ssh warns.

Done when: for every TV, ssh as root with the Pi's key works, the three flag files exist, you collected the reply of the Quick Start+ call, the owner confirms automatic updates are off and Wake-on-LAN is on, and IP and MAC are in the config.

## 5. Install on each TV and check the data flow

```
sh ~/guest-tv/deploy/tv_install.sh <tv-ip> ~/com.example.guesttv_1.0.0_all.ipk
sh ~/guest-tv/deploy/tv_hotelmode.sh <tv-ip> show
sudo systemctl restart guest-tv.service
journalctl -u guest-tv-state
python3 ~/guest-tv/tv_settings.py <tv-ip> status
```

Run on the Pi, the first two for one TV at a time. tv_install.sh installs the app, sets up the autostart, records the TV's baseline values, points the Home key at the portal, switches on hotel mode and does a cold boot test. The install counts only when LG's installer reports the state "installed"; anything else stops it and prints the installer's log. The scripts on the Pi reach a TV only with the Pi's key, so a missing key stops them with an error instead of waiting at a password prompt. Use show to look at the hotel mode state. `off` instead of `show` is the way back and not part of the install: it sets the three hotel mode switches to the values saved before the first `on` and reports an error unless the TV reads them back. The Pi builds the state file every hour and the TVs fetch it every 5 minutes.

Done when: tv_install.sh ends without error for every TV, `sh ~/guest-tv/deploy/tv_hotelmode.sh <tv-ip> show` reports hotel mode on for every TV, journalctl shows the last hourly run of guest-tv-state without errors, tv_settings.py status answers for every TV, and the owner confirms on each TV that power on and waking from standby show the portal, the Home key opens it, the Back key does not close it, the greeting shows the right guest name, stay dates and language (or the vacancy screen when nobody is booked), and that a change of sleep timer, brightness or eye comfort mode in Settings arrives in the TV's own settings, and that after a language switch the TV's system language follows.

## 6. Switch on the reset after each stay

Skip this step if the owner said no in step 1, and keep turnover off in the config.

```
python3 ~/guest-tv/turnover.py <tv-ip> normal
python3 ~/guest-tv/turnover.py <tv-ip> run --dry
python3 ~/guest-tv/turnover.py <tv-ip> run
```

Run on the Pi for each TV, in this order. Set the TV up the way it should look for every guest first, then record it with normal. The dry run shows what would be deleted and changes nothing. The real run needs the owner at the TV. With turnover on in the config, the Pi resets each TV after a departure between 11:00 and 16:00, only while no guest is in the apartment. Before 15:00 it touches only a TV that is switched off; from 15:00 it resets a running TV too. If a step fails, it switches the TV off again and retries the next hour.

Done when: for every TV, the dry run lists the files and nothing is deleted, after the real run the TV is switched off, and the owner sees Netflix, YouTube and Prime Video logged out while Disney+ and the live TV app are still logged in; then turnover is set on in ~/guest-tv/config.json. If the owner said no, turnover is off there.

## 7. Final check and hand over

Done when: the tests from step 2 still pass, and the owner confirms on every TV that both rows work with the arrow keys and with the Magic Remote pointer, the language switch works, the screensaver starts after 3 minutes without input and a TV switched on from standby shows the portal. You have told the owner the risks listed under guardrails.

## Reference: measured webOS facts

Measured on LG webOS 4.10 (2019 LCD TVs) and webOS 5.6 (2020 OLED), rooted with the webOS Homebrew Channel.

### Commands on the TV

- `luna-send -n 1` and `luna-send -w <ms> -i` print nothing on both firmwares. To get a reply, run `luna-send -i` in the background, sleep briefly, `kill` it, `wait`, then read the output file. An earlier "root may not write settings" result was this artifact.
- Never kill `com.webos.settingsservice`: upstart does not restart it and TV settings stay dead until a reboot. If TV settings stop answering, reboot the TV; `initctl restart` does not bring the service back.
- Plain `ps` shows no app processes, `ps -ef` shows them by program path. BusyBox `find` does not know `-uid` and silently matches nothing; use `-user`.

### Settings

- LG's settings service answers "Access denied" to third-party apps except for a short read-only allow list, `/etc/palm/settingsservice.allow`, read once at service start. Adding the app to a permission group does not help: the hub allows, the service still refuses. So the app sends the guest's choice to the Pi, which writes it as root over SSH.
- The Pi accepts these requests only from the configured TV addresses and with the app's origin: `file://<app-id>-webos` on webOS 5, `file://<app-id>` on webOS 4.10. A desktop browser sends `null`. A request without an origin is refused.
- Picture values apply per input. The reset brings the portal to the front before it sets them; HDMI inputs keep what a guest set there.
- The default Home app is set as root (`defaultApps.home`). Write the whole `defaultApps` object back, never only `home`, or other assignments get lost.

### Installing

- Install apps with `appInstallService/dev/install`, as the Homebrew Channel and ares-install do. `/usr/palm/applications` is a signed, read-only squashfs, so never write there.
- `appinfo.json` needs `"disableBackHistoryAPI": true`. Otherwise webOS handles Back itself and the app closes. With it, keycode 461 reaches the app.
- Web apps run at 1920x1080 (`"resolution"` in appinfo.json), also on a 4K OLED.

### Keys

- LG's hotel mode (switched on as root) locks the voice key. The Home key opens the portal. The settings (gear) key stays open: no hotel mode switch locks only that key, and the ones that lock it also lock Home.
- `enableMenuKey: off` also locks the Home key. Keep it on.

### Power

- An LG TV returns to the last input when switched on (a game console, for example), even with LG's "launch Home on start". A small script on the TV (`guesttv.init`) watches the power state and brings the portal to the front when the TV wakes from standby.
- Standby goes first to "Active Standby" (still reachable), then to "Suspend" without ping or SSH. Waking needs the TV's Wi-Fi MAC, a magic packet to the broadcast address and `wolwowlOnOff` on. After `powerOff` the TV drops off the network at once.
- Quick Start+ off means deep sleep: every power-on is a slow cold boot and waking over the network may fail. Switch it on; the TVs then woke in 14 to 21 seconds.
- `/proc/uptime` keeps counting in standby, so a jump between two checks shows the TV was asleep. After `reboot`, `/proc/sys/kernel/random/boot_id` stayed the same and only `/proc/uptime` starts again, so prove a reboot with uptime.
- A full reset takes about two to three minutes per TV; the reboot took 54 seconds on the OLED.

### Logins

- Netflix, YouTube and Prime Video are native apps. Their data sits in a writable layer over the app package. Delete only through `/media/cryptofs/apps/usr/palm/applications/<id>/`, with the app closed.
- Netflix keeps its own PlayReady store: `data/var/dpi/playready4.4/storage/drmstore` on webOS 5, `playready3.0` on webOS 4.10. Without deleting it, Netflix stayed logged in after the reset. After a new login Netflix plays again.
- Delete the content of the folders, never the folders. The Prime Video folder belongs to root; the app could not recreate its folders and started with a black screen.
- LG starts Netflix, Prime Video and YouTube in the background after every boot, and YouTube again about 6 seconds after it was closed. Close the apps, wait 3 seconds, check, delete at once.

### Reset after each stay

- A folder that is a symlink anywhere on a delete path stops the reset with exit 5, so an app cannot redirect a delete outside its own folder.
- The window between check-out and check-in is checked again before each TV, so no TV reset starts after check-in (16:00 in the template) even if the run began just before.
- A stay extended after its cleanup gets its own cleanup at the new departure.
- A booking cancelled after the stay still gets its cleanup at its departure date, since the stay may have been real. The owner's blocked dates never get one.
- Departures of the last 7 days that the Pi missed while switched off get their cleanup when it is back.

### Homebrew

- Dropbear, the SSH server, creates new host keys at every cold boot, so a remembered host key breaks the next connection. The Homebrew startup, and with it SSH, runs only at a cold boot (power plug), not after standby.
- If a TV is switched off while the Homebrew startup is still running, it may boot into "Failsafe mode" without root. The fix on the TV: choose "Reboot now". To prevent it, switch off only when `/var/luna/preferences/webosbrew_failsafe` is gone.

### Web app in the TV browser

- webOS 4 has Chromium 53 and webOS 5 has Chromium 68, so the CSS avoids grid and other newer features.
- The TV browser sized flex children to their text and cut them off with "…" although there was room. Full-width children (`align-items: stretch`) fix it. A headless desktop browser does not show this.
- `toLocaleTimeString` follows the TV's system language (it showed AM/PM), so format the clock by hand.
- On the OLED, LG's own screensaver can start after a few minutes without input.

## Guardrails

- For rooting, send the owner to the webOS Homebrew project (https://www.webosbrew.org/) and let the owner do it, only on firmware the project lists. Never run an exploit yourself; the repo contains no rooting tool. Say first that rooting is now-or-never: after an LG update it may no longer be possible, and the TV then stays on that firmware.
- Ask the owner to keep the TVs and the Pi in a network guests cannot reach, and to give guests a separate guest Wi-Fi. Rooted TVs can be controlled over the network, and the state file holds the guest's name, the stay dates and the guest Wi-Fi password, which any device in the same network could read. Use root SSH with the Pi's key only and keep telnet off.
- Add a new TV setting only as a fixed action with fixed values in pi/tv_settings.py, behind the check of TV address and app origin. Then no text from a request reaches a shell.
- When you change the code, keep an overall time limit on every download (public sources and Smoobu), so a slow server cannot hold the hourly run or the cleanup.
- Delete only the files on the fixed list, and check afterwards that they are gone. To log out another app, add its files to the list, run the dry run, then let the owner supervise a real run. A wrong delete can break an app.
- Put only information on the screen that is always true: no taglines, no help texts, nothing seasonal, no opening hours that may change. Live values older than 3 hours stay hidden. This is the template's design rule.
- Tell the owner that Open-Meteo's free API is for non-commercial use under its terms, so a rental may need their paid plan, and that webcam images and resort pages belong to their owners. Ask before you show them.
- Tell the owner these risks before handover: rooted TVs stay on firmware LG no longer patches (a known, accepted risk, so keep automatic updates off); a guest who logs out of Disney+ or the live TV app, or logs in there with an own account, is not undone by the reset; picture values on HDMI inputs stay as the guest set them.
- Publish nothing that identifies the apartment when the TVs are rooted. Keep names, towns, regions, real IPs, MAC addresses, secrets and guest data out of the repo, commits, issues and public forks, and use invented data in tests and docs.
- After any change to the app, let the owner look at it on a TV. The tests run in a desktop browser and miss TV browser problems like the cut-off text described above.
