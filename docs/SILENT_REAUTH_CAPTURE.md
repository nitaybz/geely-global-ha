# Capturing Geely's silent re-authentication (dev VM runbook)

**Goal.** Find out how the Geely phone app stays logged in for months without
ever typing an email code, so we can copy it and stop Home Assistant losing its
login every ~1-2 weeks. This is the one thing our old recordings never caught,
because it happens on Geely's encrypted channel and our captures only spanned a
day.

**Strategy.** Run the real Android app inside an emulator on the dev VM with a
Frida hook that reads every network call *inside the app, in plain text, before
it is encrypted* (a hook on OkHttp, the app's HTTP library). Log in once, then
leave it running for about two weeks until the login would normally expire. When
the app silently gets a new login, the hook records the exact call.

**What the probing already told us to look for** (2026-10-05, verified live):
- The car-control handshake (`/auth/account/session/secure`) returns a
  **`refreshToken`** that our integration currently throws away.
- There is a token endpoint, **`POST /auth/account/token`** on
  `apis.ecloudeu.com`, that exists but rejects every parameter shape we guessed
  (returns `code 8500`). The app knows the right shape. **Capturing one real
  call to this endpoint is very likely the whole solution.**
- A plain password login is blocked server-side (the key service it needs,
  `/api/sale/aes/getPublicKey`, has been under maintenance / `503` since ~April).

So in the capture, the prize is: **the app refreshing its session without a
fresh email code** - most likely a call to `/auth/account/token` with a
`refreshToken`, or a reuse of `session/secure`. Grab its URL, headers and body.

---

## Machine and one-time cost

- Runs on the dev VM (`nitaybz@10.0.0.54`, or `100.66.89.4` over Tailscale).
  24 cores / 62 GB, so it can run the emulator in software.
- The app is **arm64-only**. The VM is x86-64, so we run an **arm64** emulator
  image. That means software CPU emulation (no KVM acceleration for a foreign
  CPU). It boots slowly and feels sluggish, which is fine: we are not clicking
  around, we just need it alive and polling for ~2 weeks.
- **Tradeoff you are accepting:** Geely allows only one app session per account.
  While this capture holds the session, **Home Assistant's Geely will stay
  logged out** (and if you re-login HA, it kicks the emulator). So this is a
  "HA Geely offline for ~1-2 weeks while we catch the fix" exercise. Plan for
  that, or run it when you can spare the car integration.

> Fallback if the arm64 emulator is too slow or the anti-tamper keeps killing
> the app: the Mac already has a working arm64 emulator (`geely_re`) from the
> session-12 work (Apple Silicon runs arm64 natively, much faster). The steps
> below are identical there minus the SDK install; just skip to "Install the
> app".

---

## Step 1 - Install the tooling on the VM (one time)

```bash
ssh nitaybz@10.0.0.54
# Java (needed by the Android SDK command-line tools)
sudo apt-get update && sudo apt-get install -y openjdk-17-jdk-headless unzip

# Android command-line tools
mkdir -p ~/android/cmdline-tools && cd ~/android
curl -o cmdtools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip -q cmdtools.zip -d cmdline-tools && mv cmdline-tools/cmdline-tools cmdline-tools/latest

# Environment (add to ~/.zshrc so it survives new shells)
cat >> ~/.zshrc <<'RC'
export ANDROID_HOME=$HOME/android
export ANDROID_SDK_ROOT=$HOME/android
export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator
RC
source ~/.zshrc

# SDK packages: platform tools (adb), emulator, and an ARM64 image.
# Use google_apis (NOT ...playstore) so `adb root` works without Magisk.
yes | sdkmanager --licenses
sdkmanager "platform-tools" "emulator" "platforms;android-33" \
           "system-images;android-33;google_apis;arm64-v8a"
```

Frida (host tools + matching server):

```bash
pipx install frida-tools || pip3 install --user frida-tools
frida --version            # note this version, e.g. 17.2.x
# Download the matching arm64 server (replace X.Y.Z with the version above):
cd ~/android
curl -L -o fs.xz https://github.com/frida/frida/releases/download/X.Y.Z/frida-server-X.Y.Z-android-arm64.xz
unxz fs.xz && mv fs frida-server-arm64
```

---

## Step 2 - Create and boot the emulator (headless)

```bash
echo no | avdmanager create avd -n geely_cap -k "system-images;android-33;google_apis;arm64-v8a" -d pixel_6
# Boot headless, writable system, no snapshot. Leave this running in its own tmux window.
tmux new -d -s avd 'emulator -avd geely_cap -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect -writable-system -no-snapshot'
# Wait for boot (can take several minutes on software emulation):
adb wait-for-device
until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do sleep 5; done
echo "booted"
adb root            # google_apis image allows this
```

---

## Step 3 - Install the Geely app

Copy the split APKs from the repo (on the Mac) to the VM, then install together:

```bash
# from the Mac repo checkout:
scp apk/xapk_contents/com.geely.android.intl.apk \
    apk/xapk_contents/config.arm64_v8a.apk \
    apk/xapk_contents/config.xhdpi.apk \
    nitaybz@10.0.0.54:~/android/geely/
# on the VM:
adb install-multiple -g ~/android/geely/com.geely.android.intl.apk \
    ~/android/geely/config.arm64_v8a.apk ~/android/geely/config.xhdpi.apk
```

---

## Step 4 - Start Frida and compile the hook

```bash
# push + run frida-server
adb push ~/android/frida-server-arm64 /data/local/tmp/frida-server
adb shell "su 0 chmod 755 /data/local/tmp/frida-server"
adb shell "su 0 /data/local/tmp/frida-server -l 127.0.0.1:6789 &"
adb forward tcp:6789 tcp:6789
frida-ps -H 127.0.0.1:6789 | head      # confirm it responds

# compile the OkHttp sniffer (Frida 17 needs the Java bridge bundled in)
scp frida_agent/sniff_okhttp.js frida/anti-detect.js nitaybz@10.0.0.54:~/android/geely/
cd ~/android/geely && npm install frida-java-bridge frida-compile
./node_modules/.bin/frida-compile sniff_okhttp.js -o sniff_compiled.js
```

Attach script (`~/android/geely/attach.py`) - the repo's `run_sniff_proven.py`
was never committed, so use this equivalent. It late-attaches (most reliable
against the anti-Frida sweep), loads anti-detect first, then the sniffer, and
appends every request/response to `capture.jsonl`:

```python
import frida, sys, time, subprocess, json, os
PKG = "com.geely.android.intl"
OUT = os.path.expanduser("~/android/geely/capture.jsonl")
dev = frida.get_device_manager().add_remote_device("127.0.0.1:6789")

def pid_of():
    out = subprocess.run(["adb","shell","pidof",PKG],capture_output=True,text=True).stdout.strip()
    return int(out.split()[0]) if out else None

def on_message(msg, data):
    if msg.get("type") == "send":
        with open(OUT,"a") as f:
            f.write(json.dumps({"ts":time.time(),**msg["payload"]})+"\n")
    elif msg.get("type") == "error":
        print("FRIDA ERR:", msg.get("description"))

anti = open(os.path.expanduser("~/android/geely/anti-detect.js")).read()
hook = open(os.path.expanduser("~/android/geely/sniff_compiled.js")).read()

def attach():
    pid = pid_of()
    if not pid:
        subprocess.run(["adb","shell","am","start","-n",f"{PKG}/com.lotus.android.biz.intel.home.ui.activity.SplashActivity"])
        time.sleep(8); pid = pid_of()
    s = dev.attach(pid)
    for src in (anti, hook):
        sc = s.create_script(src, runtime="v8"); sc.on("message", on_message); sc.load()
    print("attached to", pid); return s

sess = attach()
# Watchdog: if the app is killed/restarts, re-attach. Runs forever.
while True:
    time.sleep(30)
    try: sess.is_detached
    except Exception: pass
    if sess is None or sess.is_detached:
        print("re-attaching..."); 
        try: sess = attach()
        except Exception as e: print("reattach failed:", e); time.sleep(30)
```

---

## Step 5 - Log in once, then let it run

1. **Disable HA's Geely first** so it does not fight the emulator for the single
   session. On the Ginnie PC HA box, in
   `ginnie-home/ha/.storage/core.config_entries`, set the `geely_global` entry's
   `"disabled_by": "user"`, then restart HA (`docker compose restart
   ginnie-home`). (Re-enable it when the capture is done.)
2. Start the hook in tmux: `tmux new -d -s cap 'cd ~/android/geely && python3 attach.py 2>&1 | tee attach.log'`.
3. Drive the login once. Either mirror the screen with `scrcpy` over the
   forwarded adb, or do it blind with input taps. The app sends an email code;
   read it from the nitaybz@gmail.com inbox and enter it. Confirm you can see
   the car in the app.
4. **Keep the emulator awake and the app foregrounded** so Android does not
   suspend it:
   ```bash
   adb shell svc power stayon true
   adb shell dumpsys deviceidle disable
   adb shell cmd appops set com.geely.android.intl RUN_ANY_IN_BACKGROUND allow
   ```
5. **Leave it for ~10-14 days.** The hook keeps appending to `capture.jsonl`.
   Check in every couple of days that the app is still alive
   (`adb shell pidof com.geely.android.intl`) and re-run step 2 if the watchdog
   died.

---

## Step 6 - What to pull out of the capture

When the original login would have expired (watch for the app recovering on its
own), find the recovery calls:

```bash
cd ~/android/geely
# any token / auth / login / refresh traffic, in time order:
python3 - <<'PY'
import json
for line in open("capture.jsonl"):
    d=json.loads(line)
    u=d.get("url","")
    if any(s in u for s in ("/auth/account/token","session/secure","/login","refreshToken","getPublicKey","/oauth")):
        print(d.get("ts"), d.get("kind"), d.get("method"), u)
        if d.get("body"): print("   BODY:", d["body"][:600])
PY
```

The answer we need is the **exact request** (URL, headers, body) that gets a
fresh token without an email code. Most likely one of:
- `POST /auth/account/token` with a `refreshToken` (the 8500 endpoint - now we
  see the real field names / headers it wants), or
- a second `session/secure` that reuses a stored credential, or
- (less likely) a password login, if Geely has by then re-enabled the key
  service the watcher is tracking.

Copy that request verbatim into the next session. Implementing it is small: keep
the `refreshToken` from `session/secure` (we already receive it, we just discard
it) and call the captured refresh endpoint instead of re-logging-in. That makes
the integration refresh its own session headless, for every user, and ends the
periodic sign-in expiry.

---

## Cleanup (do not skip - per repo policy, stop what you start)

```bash
adb emu kill 2>/dev/null           # stop the emulator
tmux kill-session -t avd 2>/dev/null; tmux kill-session -t cap 2>/dev/null
adb shell "su 0 pkill frida-server" 2>/dev/null
# Re-enable HA's Geely: set disabled_by back to null and restart HA, then
# re-login once with a fresh code.
```
