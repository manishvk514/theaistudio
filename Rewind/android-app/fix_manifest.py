"""Mark the compiled manifest as built for Android 16 (API 36) and opt into the new back gesture."""
import os, shutil, sys, zipfile
import pyaxml

apk = sys.argv[1]
with zipfile.ZipFile(apk) as z:
    raw = z.read("AndroidManifest.xml")
a, _ = pyaxml.AXMLGuess.from_axml(raw)
x = a.to_xml()
s = x if isinstance(x, str) else pyaxml._from_element(x)
s = s.replace('platformBuildVersionCode="23"', 'platformBuildVersionCode="36"')
s = s.replace('platformBuildVersionName="6.0.1"', 'platformBuildVersionName="16"')
s = s.replace('package="app.rewind.photos"', 'package="app.rewind.photos" android:compileSdkVersion="36" android:compileSdkVersionCodename="16"', 1)
s = s.replace('<application ', '<application android:enableOnBackInvokedCallback="true" android:requestLegacyExternalStorage="true" ', 1)
assert 'compileSdkVersion="36"' in s, "manifest stamp failed"
b = pyaxml.AXML()
b.from_xml(s)
new = b.pack()
tmp = apk + ".tmp"
with zipfile.ZipFile(apk) as zin, zipfile.ZipFile(tmp, "w") as zout:
    for item in zin.infolist():
        data = new if item.filename == "AndroidManifest.xml" else zin.read(item.filename)
        zout.writestr(item, data)
shutil.move(tmp, apk)
print("manifest stamped for API 36")
