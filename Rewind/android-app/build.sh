#!/bin/bash
# Builds Rewind-Android: aapt2 (resources), javac + d8/dx (code), zipalign, apksigner (v2 + v3).
set -eo pipefail
cd "$(dirname "$0")"
AJ=/usr/lib/android-sdk/platforms/android-23/android.jar   # resource IDs (stable since API 1)
CJ=/home/user/build/aa/aa.jar   # Android 16 classes, to compile the Java code
VC=5; VN=4.0
rm -rf out && mkdir -p out/classes out/gen out/res
aapt2 compile --dir res -o out/res/compiled.zip
aapt2 link -o out/unsigned.apk -I $AJ --manifest AndroidManifest.xml -A assets \
  --min-sdk-version 24 --target-sdk-version 36 --version-code $VC --version-name $VN \
  --java out/gen out/res/compiled.zip
javac -nowarn -source 8 -target 8 -bootclasspath $CJ:$AJ -classpath $CJ:$AJ -d out/classes $(find src out/gen -name "*.java") 2>&1 | grep -v "^Picked up\|^Note:" | grep -v "^warning" || true
[ -f out/classes/app/rewind/restore/MainActivity.class ] || { echo "compile failed"; exit 1; }
dalvik-exchange --dex --min-sdk-version=24 --output=out/classes.dex out/classes
# Stamp the manifest as built with the Android 16 SDK (the linker only knows the API 23 platform)
python3 fix_manifest.py out/unsigned.apk
(cd out && zip -q -j unsigned.apk classes.dex)
zipalign -f -p 4 out/unsigned.apk out/aligned.apk
KS=rewind-release.keystore
[ -f $KS ] || keytool -genkeypair -keystore $KS -alias rewind -keyalg RSA -keysize 3072 -validity 10000 \
  -storepass rewindapp -keypass rewindapp -dname "CN=Rewind Photo Restore, O=Rewind" >/dev/null 2>&1
apksigner sign --ks $KS --ks-pass pass:rewindapp --key-pass pass:rewindapp --min-sdk-version 24 \
  --v1-signing-enabled false --v2-signing-enabled true --v3-signing-enabled true --out out/Rewind.apk out/aligned.apk
apksigner verify -v out/Rewind.apk | head -5
ls -la out/Rewind.apk
