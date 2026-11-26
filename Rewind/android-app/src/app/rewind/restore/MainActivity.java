package app.rewind.restore;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.ContentValues;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.provider.Settings;
import android.util.Size;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Rewind for Android.
 *
 * The screens are a local web page (assets/index.html) shown in a WebView; the
 * work is done here in Java and exposed to the page as window.Rewind.
 *
 * What a normal (non-root) Android app can really reach:
 *  - the system trash (Android 11+): items "deleted" in Files / Google Photos /
 *    Gallery wait ~30 days; we list them and restore them through Android's
 *    own "restore" dialog (MediaStore.createTrashRequest).
 *  - with "All files access": hidden .trashed- files, gallery-app bins,
 *    thumbnail caches (.thumbnails, .thumbdata), WhatsApp viewed statuses,
 *    Telegram media, LOST.DIR.
 * Not reachable by any app: Android/data of other apps (Android 11+) and
 * truly erased flash (encrypted per file and trimmed).
 */
public class MainActivity extends Activity {
    static final String HOST = "appassets.androidplatform.net";
    static final String HOME = "https://" + HOST + "/index.html";
    static final int REQ_MEDIA = 10, REQ_RESTORE = 11, REQ_FILE = 12;

    WebView web;
    final Handler ui = new Handler(Looper.getMainLooper());
    final Map<String, Item> items = new HashMap<String, Item>();
    ValueCallback<Uri[]> fileCallback;
    volatile boolean scanning = false;

    static class Item {
        String id, kind, name, category, source, path, uri, why, date;
        long size, offset = -1, length = -1;
        int w, h;
        long dhash = 0;
        boolean dup = false;
    }

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0d0f12"));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        web.addJavascriptInterface(new Bridge(), "Rewind");
        web.setWebViewClient(new Client());
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
                fileCallback = cb;
                try {
                    startActivityForResult(p.createIntent(), REQ_FILE);
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });
        // Android 15+ draws apps edge to edge: keep the page clear of the status and navigation bars
        getWindow().getDecorView().setBackgroundColor(Color.parseColor("#0d0f12"));
        web.setOnApplyWindowInsetsListener(new android.view.View.OnApplyWindowInsetsListener() {
            @SuppressWarnings("deprecation")
            public android.view.WindowInsets onApplyWindowInsets(android.view.View v, android.view.WindowInsets ins) {
                v.setPadding(ins.getSystemWindowInsetLeft(), ins.getSystemWindowInsetTop(),
                        ins.getSystemWindowInsetRight(), ins.getSystemWindowInsetBottom());
                return ins;
            }
        });
        setContentView(web);
        if (Build.VERSION.SDK_INT >= 33) registerBack();
        web.loadUrl(HOME);
    }

    /** Android 13+ back gesture (Android 16 no longer calls onBackPressed for new apps). */
    void registerBack() {
        getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,
                new android.window.OnBackInvokedCallback() {
                    public void onBackInvoked() {
                        if (web.canGoBack()) web.goBack();
                        else finish();
                    }
                });
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onResume() {
        super.onResume();
        js("window.onNativeResume && onNativeResume()");
    }

    void js(final String code) {
        ui.post(new Runnable() {
            public void run() {
                if (web != null) web.evaluateJavascript(code, null);
            }
        });
    }

    static String q(String s) {
        return JSONObject.quote(s == null ? "" : s);
    }

    // --------------------------------------------------------------- asset/thumbnail server
    class Client extends WebViewClient {
        @Override
        public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest req) {
            Uri u = req.getUrl();
            if (!HOST.equals(u.getHost())) return null;
            String p = u.getPath();
            try {
                if ("/thumb".equals(p)) return thumb(u.getQueryParameter("id"));
                if ("/media".equals(p)) return media(u.getQueryParameter("id"));
                String asset = p.startsWith("/") ? p.substring(1) : p;
                String mime = asset.endsWith(".html") ? "text/html" : asset.endsWith(".css") ? "text/css"
                        : asset.endsWith(".js") ? "text/javascript" : asset.endsWith(".png") ? "image/png"
                        : asset.endsWith(".svg") ? "image/svg+xml" : "application/octet-stream";
                return new WebResourceResponse(mime, "utf-8", getAssets().open(asset));
            } catch (Exception e) {
                return new WebResourceResponse("text/plain", "utf-8", 404, "Not found",
                        new HashMap<String, String>(), new ByteArrayInputStream(new byte[0]));
            }
        }

        // API 24+ signature (not in the API 23 stubs we compile against, so no @Override)
        public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
            return handleUrl(r.getUrl());
        }

        @Override
        @SuppressWarnings("deprecation")
        public boolean shouldOverrideUrlLoading(WebView v, String url) {
            return handleUrl(Uri.parse(url));
        }

        boolean handleUrl(Uri url) {
            String host = url.getHost();
            String scheme = url.getScheme();
            if (HOST.equals(host)) return false;
            // every other address, the Mac's included, opens in the phone's browser
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, url));
            } catch (Exception ignored) {
            }
            return true;
        }
    }

    static boolean isPrivate(String host) {
        return host.startsWith("192.168.") || host.startsWith("10.") || host.startsWith("172.")
                || host.equals("localhost") || host.startsWith("127.");
    }

    byte[] readItemBytes(Item it, int max) throws Exception {
        InputStream in = openItem(it);
        try {
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            byte[] buf = new byte[65536];
            int n, total = 0;
            while ((n = in.read(buf)) > 0 && (max <= 0 || total < max)) {
                bo.write(buf, 0, n);
                total += n;
            }
            return bo.toByteArray();
        } finally {
            in.close();
        }
    }

    InputStream openItem(Item it) throws Exception {
        if (it.offset >= 0) {  // a picture carved out of a cache file
            RandomAccessFile f = new RandomAccessFile(it.path, "r");
            byte[] b = new byte[(int) it.length];
            f.seek(it.offset);
            f.readFully(b);
            f.close();
            return new ByteArrayInputStream(b);
        }
        if (it.path != null && new File(it.path).canRead()) return new FileInputStream(it.path);
        return getContentResolver().openInputStream(Uri.parse(it.uri));
    }

    Bitmap smallBitmap(Item it, int edge) {
        try {
            if ("video".equals(it.kind)) {
                if (it.uri != null && Build.VERSION.SDK_INT >= 29) {
                    return getContentResolver().loadThumbnail(Uri.parse(it.uri), new Size(edge, edge), null);
                }
                if (it.path != null)
                    return android.media.ThumbnailUtils.createVideoThumbnail(it.path, MediaStore.Images.Thumbnails.MINI_KIND);
                return null;
            }
            byte[] data = it.offset >= 0 ? readItemBytes(it, 0) : null;
            BitmapFactory.Options o = new BitmapFactory.Options();
            o.inJustDecodeBounds = true;
            if (data != null) BitmapFactory.decodeByteArray(data, 0, data.length, o);
            else {
                InputStream in = openItem(it);
                BitmapFactory.decodeStream(in, null, o);
                in.close();
            }
            if (o.outWidth <= 0) return null;
            it.w = o.outWidth;
            it.h = o.outHeight;
            int sample = 1;
            while (Math.max(o.outWidth, o.outHeight) / (sample * 2) >= edge) sample *= 2;
            BitmapFactory.Options o2 = new BitmapFactory.Options();
            o2.inSampleSize = sample;
            if (data != null) return BitmapFactory.decodeByteArray(data, 0, data.length, o2);
            InputStream in = openItem(it);
            Bitmap bm = BitmapFactory.decodeStream(in, null, o2);
            in.close();
            return bm;
        } catch (Throwable t) {
            return null;
        }
    }

    WebResourceResponse thumb(String id) throws Exception {
        Item it = items.get(id);
        Bitmap bm = it == null ? null : smallBitmap(it, 360);
        if (bm == null) return null;
        ByteArrayOutputStream bo = new ByteArrayOutputStream();
        bm.compress(Bitmap.CompressFormat.JPEG, 82, bo);
        return new WebResourceResponse("image/jpeg", null, new ByteArrayInputStream(bo.toByteArray()));
    }

    WebResourceResponse media(String id) throws Exception {
        Item it = items.get(id);
        if (it == null) return null;
        return new WebResourceResponse("video".equals(it.kind) ? "video/mp4" : "image/jpeg", null, openItem(it));
    }

    // --------------------------------------------------------------- scanning
    boolean hasMediaPermission() {
        if (Build.VERSION.SDK_INT >= 33)
            return checkSelfPermission("android.permission.READ_MEDIA_IMAGES") == PackageManager.PERMISSION_GRANTED;
        return checkSelfPermission("android.permission.READ_EXTERNAL_STORAGE") == PackageManager.PERMISSION_GRANTED;
    }

    /** Android 7-10 let an app with photo access read shared folders directly; 11+ only through the gallery. */
    boolean hasAllFiles() {
        return Build.VERSION.SDK_INT < 30 && hasMediaPermission();
    }

    void progress(String msg, int found) {
        js("onScanProgress(" + q(msg) + "," + found + ")");
    }

    void scan() {
        items.clear();
        List<Item> found = new ArrayList<Item>();
        try {
            if (Build.VERSION.SDK_INT >= 30 && hasMediaPermission()) {
                progress("Looking in the phone's trash…", 0);
                queryMedia(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, "image", true, found);
                queryMedia(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, "video", true, found);
            }
            if (hasAllFiles()) {
                File root = Environment.getExternalStorageDirectory();
                String[][] places = {
                        {"DCIM", "deep"}, {"Pictures", "deep"}, {"Movies", "trash"}, {"Download", "trash"},
                        {"Android/media/com.whatsapp/WhatsApp/Media/.Statuses", "cache"},
                        {"WhatsApp/Media/.Statuses", "cache"},
                        {"Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/Private", "cache"},
                        {"Telegram", "cache"}, {"Android/media/org.telegram.messenger", "cache"},
                        {"Android/.Trash", "bin"}, {".Trash", "bin"}, {".RecycleBin", "bin"},
                        {"MIUI/Gallery/cloud/.trashBin", "bin"}, {"MIUI/Gallery/cloud/.cache", "cache"},
                        {".Gallery2", "cache"}, {"LOST.DIR", "bin"}, {"Snapchat", "cache"},
                        {"Android/media/com.snapchat.android", "cache"},
                        {"Android/media/com.whatsapp.w4b/WhatsApp Business/Media/.Statuses", "cache"},
                        {"Android/media/com.instagram.android", "cache"},
                        {"Android/media/com.google.android.apps.photos", "cache"},
                        {"Android/media/com.coloros.gallery3d", "cache"}, {"Android/media/com.oplus.gallery3d", "cache"},
                        {".FileManagerRecycler", "bin"}, {".recycle", "bin"}, {"Recycle", "bin"},
                        {"Pictures/.thumbnails", "thumbs"}, {"DCIM/.thumbnails", "thumbs"},
                };
                for (String[] pl : places) {
                    File d = new File(root, pl[0]);
                    if (!d.exists()) continue;
                    progress("Searching " + pl[0] + "…", found.size());
                    walk(d, pl[1], 0, found);
                }
            }
            progress("Comparing with the photos you still have…", found.size());
            markDuplicates(found);
        } catch (Throwable t) {
            js("toast(" + q("Scan problem: " + t) + ")");
        }
        JSONArray arr = new JSONArray();
        try {
            for (Item it : found) {
                items.put(it.id, it);
                JSONObject o = new JSONObject();
                o.put("id", it.id).put("kind", it.kind).put("name", it.name).put("category", it.category)
                        .put("source", it.source).put("why", it.why).put("date", it.date == null ? "" : it.date)
                        .put("size", it.size).put("dup", it.dup).put("w", it.w).put("h", it.h)
                        .put("trash", it.id.startsWith("t:"));
                arr.put(o);
            }
        } catch (Exception ignored) {
        }
        scanning = false;
        JSONObject stats = new JSONObject();
        try {
            stats.put("gallery", countGallery(MediaStore.Images.Media.EXTERNAL_CONTENT_URI)
                    + countGallery(MediaStore.Video.Media.EXTERNAL_CONTENT_URI));
        } catch (Exception ignored) {
        }
        js("onScanDone(" + arr.toString() + "," + stats.toString() + ")");
    }

    /** How many photos/videos are in the gallery now: the "still here" side. */
    int countGallery(Uri base) {
        if (!hasMediaPermission()) return 0;
        try {
            Cursor c = getContentResolver().query(base, new String[]{"_id"}, null, null, null);
            if (c == null) return 0;
            int n = c.getCount();
            c.close();
            return n;
        } catch (Throwable t) {
            return 0;
        }
    }

    void queryMedia(Uri base, String kind, boolean trashedOnly, List<Item> out) {
        try {
            Bundle args = new Bundle();
            args.putInt("android:query-arg-match-trashed", 3); // MATCH_ONLY
            String[] proj = {"_id", "_display_name", "_size", "datetaken", "date_expires", "_data", "width", "height"};
            if (Build.VERSION.SDK_INT < 30) return;
            Cursor c = getContentResolver().query(base, proj, args, null);
            if (c == null) return;
            while (c.moveToNext()) {
                Item it = new Item();
                long id = c.getLong(0);
                it.id = "t:" + kind + ":" + id;
                it.kind = kind;
                it.name = c.getString(1);
                it.size = c.getLong(2);
                long taken = c.isNull(3) ? 0 : c.getLong(3);
                long expires = c.isNull(4) ? 0 : c.getLong(4);
                it.date = taken > 0 ? new java.text.SimpleDateFormat("yyyy-MM-dd").format(new java.util.Date(taken)) : "";
                it.path = c.getString(5);
                it.w = c.getInt(6);
                it.h = c.getInt(7);
                it.uri = ContentUris.withAppendedId(base, id).toString();
                it.category = "trash";
                it.source = "Phone trash";
                it.why = "In Android's trash" + (expires > 0 ? ", erased on " + new java.text.SimpleDateFormat("d MMM").format(
                        new java.util.Date(expires * 1000)) : "") + ". Restore puts it back in your gallery.";
                out.add(it);
            }
            c.close();
        } catch (Throwable t) {
            // Android < 11 or blocked: nothing to list
        }
    }

    static String kindOf(byte[] h) {
        if (h.length < 12) return null;
        if ((h[0] & 0xff) == 0xff && (h[1] & 0xff) == 0xd8 && (h[2] & 0xff) == 0xff) return "image";
        if ((h[0] & 0xff) == 0x89 && h[1] == 'P' && h[2] == 'N' && h[3] == 'G') return "image";
        if (h[0] == 'G' && h[1] == 'I' && h[2] == 'F') return "image";
        if (h[0] == 'R' && h[1] == 'I' && h[2] == 'F' && h[3] == 'F' && h[8] == 'W') return "image";
        if (h[4] == 'f' && h[5] == 't' && h[6] == 'y' && h[7] == 'p') {
            String brand = new String(h, 8, 4);
            if (brand.startsWith("hei") || brand.startsWith("mif") || brand.startsWith("avi")) return "image";
            return "video";
        }
        return null;
    }

    void walk(File dir, String mode, int depth, List<Item> out) {
        if (depth > 6 || out.size() > 20000) return;
        File[] files = dir.listFiles();
        if (files == null) return;
        for (File f : files) {
            if (f.isDirectory()) {
                String n = f.getName().toLowerCase();
                String sub = mode;
                if (n.contains("thumbnail")) sub = "thumbs";
                else if (n.contains("trash") || n.contains("recycle") || n.contains("deleted")) sub = "bin";
                else if (n.equals(".statuses") || n.contains("cache")) sub = "cache";
                if ("deep".equals(mode) && sub.equals("deep") && depth >= 2) continue;
                walk(f, sub, depth + 1, out);
                continue;
            }
            String name = f.getName();
            String low = name.toLowerCase();
            boolean trashed = low.startsWith(".trashed-") || low.startsWith("trashed_");
            if (low.contains(".thumbdata")) {
                carve(f, out);
                continue;
            }
            if ("deep".equals(mode) && !trashed) continue; // normal camera photos are your library
            byte[] head = new byte[16];
            try {
                FileInputStream in = new FileInputStream(f);
                int r = in.read(head);
                in.close();
                if (r < 12) continue;
            } catch (Exception e) {
                continue;
            }
            String kind = kindOf(head);
            if (kind == null || f.length() < 1024) continue;
            Item it = new Item();
            it.id = "f:" + out.size();
            it.kind = kind;
            it.path = f.getAbsolutePath();
            it.name = trashed ? name.replaceFirst("^\\.?trashed[-_]\\d*-?", "") : name;
            it.size = f.length();
            it.date = new java.text.SimpleDateFormat("yyyy-MM-dd").format(new java.util.Date(f.lastModified()));
            String where = it.path.replace(Environment.getExternalStorageDirectory().getAbsolutePath() + "/", "");
            if (trashed || "bin".equals(mode)) {
                it.category = "trash";
                it.why = "Waiting in a trash bin: " + where;
            } else if ("thumbs".equals(mode)) {
                it.category = "thumbnail";
                it.why = "A preview the phone kept: " + where;
            } else {
                it.category = "recovered";
                it.why = "A copy kept in an app cache: " + where;
            }
            it.source = where.contains("whatsapp") || where.contains("WhatsApp") ? "WhatsApp"
                    : where.contains("elegram") ? "Telegram" : where.contains("MIUI") ? "Xiaomi gallery" : "Phone storage";
            out.add(it);
        }
    }

    /** .thumbdata files: many JPEG previews glued together. Cut each one out. */
    void carve(File f, List<Item> out) {
        try {
            RandomAccessFile raf = new RandomAccessFile(f, "r");
            long len = raf.length();
            if (len > 512L * 1024 * 1024) {
                raf.close();
                return;
            }
            byte[] all = new byte[(int) len];
            raf.readFully(all);
            raf.close();
            int i = 0, n = 0;
            while (i < all.length - 3) {
                if ((all[i] & 0xff) == 0xff && (all[i + 1] & 0xff) == 0xd8 && (all[i + 2] & 0xff) == 0xff) {
                    int j = i + 2;
                    int end = -1;
                    while (j < all.length - 1) {
                        if ((all[j] & 0xff) == 0xff && (all[j + 1] & 0xff) == 0xd9) {
                            end = j + 2;
                            break;
                        }
                        j++;
                    }
                    if (end < 0) break;
                    if (end - i > 2048) {
                        Item it = new Item();
                        it.id = "c:" + out.size();
                        it.kind = "image";
                        it.path = f.getAbsolutePath();
                        it.offset = i;
                        it.length = end - i;
                        it.size = it.length;
                        it.name = "preview_" + (++n) + ".jpg";
                        it.category = "thumbnail";
                        it.source = "Thumbnail cache";
                        it.why = "Cut out of the phone's thumbnail cache (" + f.getName() + ")";
                        out.add(it);
                    }
                    i = end;
                } else i++;
            }
        } catch (Throwable ignored) {
        }
    }

    static long dhash(Bitmap bm) {
        if (bm == null) return 0;
        Bitmap s = Bitmap.createScaledBitmap(bm, 9, 8, true);
        long h = 0;
        for (int y = 0; y < 8; y++)
            for (int x = 0; x < 8; x++) {
                int a = s.getPixel(x, y), b = s.getPixel(x + 1, y);
                int la = (Color.red(a) * 299 + Color.green(a) * 587 + Color.blue(a) * 114);
                int lb = (Color.red(b) * 299 + Color.green(b) * 587 + Color.blue(b) * 114);
                h = (h << 1) | (la > lb ? 1 : 0);
            }
        return h;
    }

    /** Hide previews and cache copies of photos that are still in the gallery. */
    void markDuplicates(List<Item> found) {
        List<Item> cands = new ArrayList<Item>();
        for (Item it : found) if (!"trash".equals(it.category) && "image".equals(it.kind)) cands.add(it);
        if (cands.isEmpty() || !hasMediaPermission()) return;
        for (Item it : cands) it.dhash = dhash(smallBitmap(it, 64));
        List<Long> lib = new ArrayList<Long>();
        try {
            Cursor c = getContentResolver().query(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, new String[]{"_id"},
                    null, null, "date_added DESC");
            int k = 0;
            boolean lt = Build.VERSION.SDK_INT >= 29;
            while (c != null && c.moveToNext() && k < 6000) {
                k++;
                if (k % 200 == 0) progress("Comparing with your gallery… " + k, found.size());
                Uri u = ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, c.getLong(0));
                Bitmap bm = null;
                try {
                    bm = lt ? getContentResolver().loadThumbnail(u, new Size(64, 64), null)
                            : MediaStore.Images.Thumbnails.getThumbnail(getContentResolver(), c.getLong(0), MediaStore.Images.Thumbnails.MICRO_KIND, null);
                } catch (Throwable ignored) {
                }
                if (bm != null) lib.add(dhash(bm));
            }
            if (c != null) c.close();
        } catch (Throwable ignored) {
        }
        for (Item it : cands) {
            if (it.dhash == 0) continue;
            for (long h : lib) {
                if (Long.bitCount(h ^ it.dhash) <= 6) {
                    it.dup = true;
                    break;
                }
            }
        }
    }

    // --------------------------------------------------------------- restoring
    int saveToGallery(List<Item> list) {
        int ok = 0;
        for (Item it : list) {
            try {
                ContentValues cv = new ContentValues();
                String name = it.name == null ? "restored.jpg" : it.name;
                cv.put("_display_name", name);
                boolean video = "video".equals(it.kind);
                cv.put("mime_type", video ? "video/mp4" : (name.toLowerCase().endsWith(".png") ? "image/png" : "image/jpeg"));
                Uri base = video ? MediaStore.Video.Media.EXTERNAL_CONTENT_URI : MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
                if (Build.VERSION.SDK_INT >= 29) cv.put("relative_path", (video ? "Movies" : "Pictures") + "/Rewind");
                Uri dest = getContentResolver().insert(base, cv);
                if (dest == null) continue;
                OutputStream os = getContentResolver().openOutputStream(dest);
                InputStream in = openItem(it);
                byte[] buf = new byte[65536];
                int n;
                while ((n = in.read(buf)) > 0) os.write(buf, 0, n);
                in.close();
                os.close();
                ok++;
            } catch (Exception ignored) {
            }
        }
        return ok;
    }

    void restoreTrash(List<Item> list) {
        try {
            List<Uri> uris = new ArrayList<Uri>();
            for (Item it : list) uris.add(Uri.parse(it.uri));
            if (Build.VERSION.SDK_INT < 30) return;
            PendingIntent pi = MediaStore.createTrashRequest(getContentResolver(), uris, false);
            startIntentSenderForResult(pi.getIntentSender(), REQ_RESTORE, null, 0, 0, 0);
        } catch (Throwable t) {
            js("toast(" + q("Android could not restore these: " + t.getClass().getSimpleName()) + ")");
        }
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == REQ_FILE && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res, data));
            fileCallback = null;
            return;
        }
        if (req == REQ_RESTORE) {
            js(res == RESULT_OK ? "onRestored(true)" : "onRestored(false)");
        }
        super.onActivityResult(req, res, data);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] p, int[] r) {
        js("onPermissions()");
    }

    // --------------------------------------------------------------- bridge
    class Bridge {
        @JavascriptInterface
        public String status() {
            try {
                SharedPreferences sp = getSharedPreferences("rewind", MODE_PRIVATE);
                return new JSONObject().put("media", hasMediaPermission()).put("allFiles", hasAllFiles())
                        .put("sdk", Build.VERSION.SDK_INT).put("trashApi", Build.VERSION.SDK_INT >= 30)
                        .put("mac", sp.getString("mac", "")).put("model", Build.MANUFACTURER + " " + Build.MODEL).toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        @JavascriptInterface
        public void askMedia() {
            ui.post(new Runnable() {
                public void run() {
                    if (Build.VERSION.SDK_INT >= 34)
                        requestPermissions(new String[]{"android.permission.READ_MEDIA_IMAGES", "android.permission.READ_MEDIA_VIDEO",
                                "android.permission.READ_MEDIA_VISUAL_USER_SELECTED"}, REQ_MEDIA);
                    else if (Build.VERSION.SDK_INT >= 33)
                        requestPermissions(new String[]{"android.permission.READ_MEDIA_IMAGES", "android.permission.READ_MEDIA_VIDEO"}, REQ_MEDIA);
                    else requestPermissions(new String[]{"android.permission.READ_EXTERNAL_STORAGE"}, REQ_MEDIA);
                }
            });
        }

        @JavascriptInterface
        public void askAllFiles() {
            askMedia();
        }

        @JavascriptInterface
        public void scan() {
            if (scanning) return;
            scanning = true;
            new Thread(new Runnable() {
                public void run() {
                    MainActivity.this.scan();
                }
            }).start();
        }

        List<Item> pick(String idsJson) {
            List<Item> list = new ArrayList<Item>();
            try {
                JSONArray a = new JSONArray(idsJson);
                for (int i = 0; i < a.length(); i++) {
                    Item it = items.get(a.getString(i));
                    if (it != null) list.add(it);
                }
            } catch (Exception ignored) {
            }
            return list;
        }

        @JavascriptInterface
        public void restore(final String idsJson) {
            new Thread(new Runnable() {
                public void run() {
                    List<Item> trash = new ArrayList<Item>(), files = new ArrayList<Item>();
                    for (Item it : pick(idsJson)) (it.id.startsWith("t:") ? trash : files).add(it);
                    if (!files.isEmpty()) {
                        int n = saveToGallery(files);
                        js("onSaved(" + n + ")");
                    }
                    if (!trash.isEmpty()) {
                        final List<Item> t = trash;
                        ui.post(new Runnable() {
                            public void run() {
                                restoreTrash(t);
                            }
                        });
                    }
                }
            }).start();
        }

        @JavascriptInterface
        public void openMac(String address) {
            String a = address.trim();
            if (!a.startsWith("http")) a = "http://" + a;
            getSharedPreferences("rewind", MODE_PRIVATE).edit().putString("mac", address.trim()).apply();
            final String url = a;
            ui.post(new Runnable() {
                public void run() {
                    try {
                        startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                    } catch (Exception e) {
                        js("toast('No browser found to open your Mac')");
                    }
                }
            });
        }
    }
}
