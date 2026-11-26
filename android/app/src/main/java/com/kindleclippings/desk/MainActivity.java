package com.kindleclippings.desk;

import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final String HOST = "appassets.androidplatform.net";
    private static final int OPEN_TEXT = 1;
    private static final int SAVE_EXPORT = 2;
    private static final int MAX_BODY = 64 * 1024 * 1024;
    private static final ExecutorService WORKER = Executors.newSingleThreadExecutor();
    private static PyObject pythonBridge;

    private WebView web;
    private ValueCallback<Uri[]> fileChoice;
    private Export pendingExport;

    @Override
    public void onCreate(Bundle savedState) {
        super.onCreate(savedState);
        if (savedState != null && savedState.containsKey("exportPath")) {
            pendingExport = new Export(-1, savedState.getString("exportName"),
                    new File(savedState.getString("exportPath")), savedState.getString("exportMime"));
        }

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xfff4f2eb);
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                Insets padding = insets.getInsets(WindowInsets.Type.systemBars()
                        | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(padding.left, padding.top, padding.right, padding.bottom);
            }
            return insets;
        });
        web = new WebView(this);
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setAllowFileAccess(false);
        web.addJavascriptInterface(new AndroidBridge(), "ClippingsAndroid");
        web.setWebViewClient(new LocalPages());
        web.setWebChromeClient(new TextFileChooser());
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        web.loadUrl("https://" + HOST + "/");
    }

    private static boolean isLocal(Uri uri) {
        return "https".equals(uri.getScheme()) && HOST.equals(uri.getHost()) && uri.getPort() == -1;
    }

    private static String assetMime(String path) {
        if (path == null) return null;
        switch (path) {
            case "/":
            case "/index.html": return "text/html";
            case "/demo-clippings.txt": return "text/plain";
            case "/assets/app.js": return "text/javascript";
            case "/assets/styles.css": return "text/css";
            case "/assets/hero.svg":
            case "/assets/favicon.svg": return "image/svg+xml";
            default: return null;
        }
    }

    private final class LocalPages extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            return !request.isForMainFrame() || !isLocal(uri)
                    || !("/".equals(uri.getPath()) || "/index.html".equals(uri.getPath()));
        }

        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            String path = uri.getPath();
            String mime = assetMime(path);
            if (isLocal(uri) && mime != null && "GET".equals(request.getMethod())) {
                String asset = "/".equals(path) ? "index.html" : path.substring(1);
                try {
                    InputStream input = getAssets().open(asset);
                    Map<String, String> headers = new HashMap<>();
                    headers.put("X-Content-Type-Options", "nosniff");
                    headers.put("Content-Security-Policy", "default-src 'none'; script-src 'self'; "
                            + "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                            + "connect-src 'self'; frame-src 'none'; base-uri 'none'; form-action 'none'");
                    return new WebResourceResponse(mime, "UTF-8", 200, "OK", headers, input);
                } catch (IOException error) {
                    Log.e("ClippingsDesk", "Cannot open app asset", error);
                }
            }
            return new WebResourceResponse("text/plain", "UTF-8", 404, "Not Found",
                    null, new ByteArrayInputStream(new byte[0]));
        }
    }

    private final class TextFileChooser extends WebChromeClient {
        @Override
        public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                         FileChooserParams params) {
            if (fileChoice != null) fileChoice.onReceiveValue(null);
            fileChoice = callback;
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT)
                    .addCategory(Intent.CATEGORY_OPENABLE)
                    .setType("*/*")
                    .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
            try {
                startActivityForResult(intent, OPEN_TEXT);
            } catch (RuntimeException error) {
                fileChoice.onReceiveValue(null);
                fileChoice = null;
                notifyUser("无法打开系统文件选择器。");
            }
            return true;
        }
    }

    private PyObject bridge() {
        if (pythonBridge == null) {
            if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
            PyObject module = Python.getInstance().getModule("android_bridge");
            module.callAttr("initialize", getFilesDir().getAbsolutePath());
            pythonBridge = module;
        }
        return pythonBridge;
    }

    public final class AndroidBridge {
        @JavascriptInterface
        public void request(int id, String route, String body) {
            WORKER.execute(() -> {
                try {
                    if (body.length() > MAX_BODY) throw new IllegalArgumentException("剪藏文件过大。");
                    if ("/api/export".equals(route)) {
                        List<PyObject> result = bridge().callAttr("export_file", body,
                                getCacheDir().getAbsolutePath()).asList();
                        Export export = new Export(id, result.get(0).toString(),
                                new File(result.get(1).toString()), result.get(2).toString());
                        runOnUiThread(() -> chooseSaveLocation(export));
                    } else {
                        respond(id, bridge().callAttr("request_json", route, body).toString());
                    }
                } catch (Exception error) {
                    fail(id, error);
                }
            });
        }
    }

    private void chooseSaveLocation(Export export) {
        if (isDestroyed() || isFinishing() || pendingExport != null) {
            export.file.delete();
            respond(export.id, "{\"error\":\"请先完成当前导出。\"}");
            return;
        }
        pendingExport = export;
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT)
                .addCategory(Intent.CATEGORY_OPENABLE)
                .setType(export.mime)
                .putExtra(Intent.EXTRA_TITLE, export.name);
        try {
            startActivityForResult(intent, SAVE_EXPORT);
        } catch (RuntimeException error) {
            pendingExport = null;
            export.file.delete();
            fail(export.id, error);
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == OPEN_TEXT && fileChoice != null) {
            Uri[] files = resultCode == RESULT_OK ? selectedFiles(data) : null;
            fileChoice.onReceiveValue(files);
            fileChoice = null;
        } else if (requestCode == SAVE_EXPORT && pendingExport != null) {
            Export export = pendingExport;
            pendingExport = null;
            Uri destination = resultCode == RESULT_OK && data != null ? data.getData() : null;
            if (destination == null) {
                export.file.delete();
                respond(export.id, "{\"cancelled\":true}");
            } else {
                WORKER.execute(() -> storeExport(export, destination));
            }
        }
    }

    private static Uri[] selectedFiles(Intent data) {
        if (data == null) return null;
        ClipData multiple = data.getClipData();
        if (multiple == null) {
            Uri uri = data.getData();
            return uri != null && "content".equals(uri.getScheme()) ? new Uri[]{uri} : null;
        }
        Uri[] files = new Uri[multiple.getItemCount()];
        for (int i = 0; i < files.length; i++) {
            Uri uri = multiple.getItemAt(i).getUri();
            if (uri == null || !"content".equals(uri.getScheme())) return null;
            files[i] = uri;
        }
        return files;
    }

    private void storeExport(Export export, Uri destination) {
        try {
            if (!"content".equals(destination.getScheme())) throw new IOException("保存位置无效。");
            try (InputStream input = new FileInputStream(export.file);
                 OutputStream output = getContentResolver().openOutputStream(destination, "wt")) {
                if (output == null) throw new IOException("无法打开保存位置。");
                byte[] buffer = new byte[32 * 1024];
                int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            }
            respond(export.id, "{\"name\":" + JSONObject.quote(export.name) + "}");
            runOnUiThread(() -> notifyUser("已保存 " + export.name));
        } catch (IOException | RuntimeException error) {
            fail(export.id, error);
            runOnUiThread(() -> notifyUser("保存失败。"));
        } finally {
            export.file.delete();
        }
    }

    private void fail(int id, Exception error) {
        Log.e("ClippingsDesk", "Android request failed", error);
        String message = error.getMessage() == null ? "应用处理失败。" : error.getMessage();
        respond(id, "{\"error\":" + JSONObject.quote(message) + "}");
    }

    private void respond(int id, String json) {
        if (id < 0) return;
        runOnUiThread(() -> {
            if (!isDestroyed() && web != null) {
                web.evaluateJavascript("window.clippingsAndroidResult?.(" + id + ","
                        + JSONObject.quote(json) + ")", null);
            }
        });
    }

    private void notifyUser(String message) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
    }

    @Override
    protected void onSaveInstanceState(Bundle state) {
        super.onSaveInstanceState(state);
        if (pendingExport != null) {
            state.putString("exportName", pendingExport.name);
            state.putString("exportPath", pendingExport.file.getAbsolutePath());
            state.putString("exportMime", pendingExport.mime);
        }
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (fileChoice != null) fileChoice.onReceiveValue(null);
        ((FrameLayout) web.getParent()).removeView(web);
        web.destroy();
        web = null;
        super.onDestroy();
    }

    private static final class Export {
        final int id;
        final String name;
        final File file;
        final String mime;

        Export(int id, String name, File file, String mime) {
            this.id = id;
            this.name = name;
            this.file = file;
            this.mime = mime;
        }
    }
}
