package com.oas.oasx.host;

import android.content.Context;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.view.Surface;
import android.graphics.Bitmap;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;

/**
 * 由 Shizuku(shell uid) 通过 app_process 启动的「无头虚拟屏」宿主。
 *
 * 屏的 owner 是本进程（shell uid）：主屏完全看不到，且不随任何 App 的前后台变化，
 * 这才是「真·后台」。
 *
 * 实测 MuMu(Android 12) 上 `DisplayManagerGlobal#createVirtualDisplay` 的签名是
 *   (Context, MediaProjection, VirtualDisplayConfig, Callback, Handler, Context)
 * 所以要用反射构造 `VirtualDisplayConfig`（Builder: name/w/h/dpi + setFlags）。
 *
 * stdout 协议：`DISPLAY_ID=<n>` / `HOST_ERROR=<...>` / `PROBE=<...>`
 */
public final class VdHost {

    private static final String NAME = "oas-vdhost";
    private static ImageReader imageReader;

    public static void main(String[] args) {
        int width = args.length > 0 ? parseInt(args[0], 1280) : 1280;
        int height = args.length > 1 ? parseInt(args[1], 720) : 720;
        int dpi = args.length > 2 ? parseInt(args[2], 240) : 240;

        try {
            android.os.Looper.prepareMainLooper();
            emit("PROBE=looper ready");
            Context context = systemContext();
            imageReader = ImageReader.newInstance(width, height, android.graphics.PixelFormat.RGBA_8888, 3);
            emit("PROBE=imageReader ready");
            VirtualDisplay display = createViaGlobal(context, width, height, dpi);
            if (display == null) {
                display = createViaDisplayManager(context, width, height, dpi);
            }
            if (display == null) {
                emit("HOST_ERROR=all strategies failed");
                System.exit(2);
                return;
            }
            int createdId = display.getDisplay().getDisplayId();
            emit("DISPLAY_ID=" + createdId);
            selfTestFrame();
            startFrameServer();

            Object lock = new Object();
            //noinspection InfiniteLoopStatement
            while (true) {
                synchronized (lock) {
                    lock.wait(60_000L);
                }
            }
        } catch (Throwable error) {
            emit("HOST_ERROR=" + error.getClass().getSimpleName() + ": " + error.getMessage());
            error.printStackTrace(System.out);
            System.exit(1);
        }
    }

    private static void emit(String line) {
        System.out.println(line);
        System.out.flush();
    }

    /** 拿系统 Context（app_process 里通过 ActivityThread 构造）。 */
    private static Context systemContext() {
        try {
            Class<?> activityThread = Class.forName("android.app.ActivityThread");
            Method systemMain = activityThread.getDeclaredMethod("systemMain");
            systemMain.setAccessible(true);
            Object thread = systemMain.invoke(null);
            Method getSystemContext = activityThread.getDeclaredMethod("getSystemContext");
            getSystemContext.setAccessible(true);
            Object context = getSystemContext.invoke(thread);
            emit("PROBE=systemContext " + (context != null));
            return asShellContext((Context) context);
        } catch (Throwable error) {
            emit("PROBE=systemContext failed " + causeChain(error));
            return contextViaContextImpl();
        }
    }

    private static Object buildConfig(int width, int height, int dpi, int flags) throws Exception {
        Class<?> builderClass = Class.forName("android.hardware.display.VirtualDisplayConfig$Builder");
        Constructor<?> constructor = builderClass.getConstructor(
                String.class, int.class, int.class, int.class);
        Object builder = constructor.newInstance(NAME, width, height, dpi);

        Method setFlags = null;
        for (Method method : builderClass.getMethods()) {
            if ("setFlags".equals(method.getName()) && method.getParameterTypes().length == 1) {
                setFlags = method;
                break;
            }
        }
        if (setFlags != null) {
            setFlags.invoke(builder, flags);
        }
        for (Method method : builderClass.getMethods()) {
            if ("setSurface".equals(method.getName()) && method.getParameterTypes().length == 1) {
                method.invoke(builder, imageReader.getSurface());
                break;
            }
        }
        Method build = builderClass.getMethod("build");
        return build.invoke(builder);
    }

    /** 策略一：DisplayManagerGlobal#createVirtualDisplay(Context, MediaProjection, VirtualDisplayConfig, ...) */
    private static VirtualDisplay createViaGlobal(Context baseContext, int width, int height, int dpi) {
        // 实测本机宿主 uid=0（root 启动的 Shizuku），「packageName 必须匹配调用 uid」天然不成立，
        // 所以按候选包名逐个试，谁过就用谁。
        // 关键：不带 OWN_CONTENT_ONLY 会被判成「屏幕共享」而要求 CAPTURE_VIDEO_OUTPUT 权限。
        // PRESENTATION 会让系统按「外接显示」对待 → App 才会正常往这块屏渲染；
        // 缺了它实测「任务已 Resumed 但抓到的帧全黑」。
        int[][] flagSets = {
                {DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC
                        | DisplayManager.VIRTUAL_DISPLAY_FLAG_PRESENTATION
                        | DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY},
                {DisplayManager.VIRTUAL_DISPLAY_FLAG_PRESENTATION
                        | DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY},
                {DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC
                        | DisplayManager.VIRTUAL_DISPLAY_FLAG_PRESENTATION},
        };
        String[] candidates = {"com.android.shell"};
        for (int[] flags : flagSets) {
            for (String candidate : candidates) {
                Context attemptContext = candidate == null
                        ? baseContext
                        : new android.content.ContextWrapper(baseContext) {
                            @Override
                            public String getPackageName() {
                                return candidate;
                            }

                            @Override
                            public String getOpPackageName() {
                                return candidate;
                            }
                        };
                VirtualDisplay display = createViaGlobalOnce(
                        attemptContext, width, height, dpi, candidate, flags[0]);
                if (display != null) {
                    return display;
                }
            }
        }
        return null;
    }

    private static VirtualDisplay createViaGlobalOnce(Context context, int width, int height,
                                                      int dpi, String packageLabel, int flags) {
        try {
            Class<?> globalClass = Class.forName("android.hardware.display.DisplayManagerGlobal");
            Method getInstance = globalClass.getDeclaredMethod("getInstance");
            getInstance.setAccessible(true);
            Object global = getInstance.invoke(null);
            Object config = buildConfig(width, height, dpi, flags);

            for (Method method : globalClass.getDeclaredMethods()) {
                if (!"createVirtualDisplay".equals(method.getName())) {
                    continue;
                }
                Class<?>[] types = method.getParameterTypes();
                if (types.length != 6) {
                    continue;
                }
                method.setAccessible(true);
                Object result = method.invoke(global, context, null, config, null,
                        (Handler) null, context);
                if (result instanceof VirtualDisplay) {
                    emit("PROBE=global createVirtualDisplay ok");
                    return (VirtualDisplay) result;
                }
            }
            emit("PROBE=no 6-arg createVirtualDisplay");
        } catch (Throwable error) {
            Throwable cause = error.getCause() == null ? error : error.getCause();
            emit("PROBE=global failed pkg=" + packageLabel + " flags=" + flags + " " + cause);
        }
        return null;
    }

    /** 策略二：公开 API DisplayManager.createVirtualDisplay(name,w,h,dpi,surface,flags)。 */
    private static VirtualDisplay createViaDisplayManager(Context context, int width, int height, int dpi) {
        try {
            if (context == null) {
                emit("PROBE=no context for DisplayManager strategy");
                return null;
            }
            DisplayManager manager = (DisplayManager) context.getSystemService(Context.DISPLAY_SERVICE);
            if (manager == null) {
                emit("PROBE=DisplayManager null");
                return null;
            }
            VirtualDisplay display = manager.createVirtualDisplay(
                    NAME, width, height, dpi, (Surface) null,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC);
            emit("PROBE=DisplayManager.createVirtualDisplay ok");
            return display;
        } catch (Throwable error) {
            Throwable cause = error.getCause() == null ? error : error.getCause();
            emit("PROBE=displayManager failed " + cause);
            return null;
        }
    }

    /** 兜底：直接构造系统 ContextImpl（不依赖 ActivityThread.systemMain 成功）。 */
    private static Context contextViaContextImpl() {
        try {
            Class<?> activityThreadClass = Class.forName("android.app.ActivityThread");
            Object thread = null;
            for (String name : new String[]{"currentActivityThread", "currentActivityThread"}) {
                try {
                    java.lang.reflect.Method method = activityThreadClass.getDeclaredMethod(name);
                    method.setAccessible(true);
                    thread = method.invoke(null);
                } catch (Throwable ignored) {
                }
                if (thread != null) break;
            }
            if (thread == null) {
                emit("PROBE=contextImpl skipped (no ActivityThread)");
                return null;
            }
            Class<?> contextImplClass = Class.forName("android.app.ContextImpl");
            java.lang.reflect.Method create = contextImplClass.getDeclaredMethod(
                    "createSystemContext", activityThreadClass);
            create.setAccessible(true);
            Object context = create.invoke(null, thread);
            emit("PROBE=contextImpl " + (context != null));
            return (Context) context;
        } catch (Throwable error) {
            emit("PROBE=contextImpl failed " + causeChain(error));
            return null;
        }
    }

    private static String causeChain(Throwable error) {
        StringBuilder builder = new StringBuilder();
        Throwable current = error;
        int depth = 0;
        while (current != null && depth < 4) {
            builder.append(current.getClass().getSimpleName())
                    .append(':')
                    .append(current.getMessage())
                    .append(" | ");
            current = current.getCause();
            depth++;
        }
        return builder.toString();
    }

    /**
     * 系统 Context 的包名是 `android`，而 createVirtualDisplay 会校验
     * 「packageName 必须匹配调用方 uid」；本进程是 shell(2000) → 必须是 `com.android.shell`。
     */
    private static Context asShellContext(Context base) {
        if (base == null) {
            return null;
        }
        final String ownerPackage = ownerPackage(base);
        emit("PROBE=uid=" + android.os.Process.myUid() + " package=" + ownerPackage);
        return new android.content.ContextWrapper(base) {
            @Override
            public String getPackageName() {
                return ownerPackage;
            }

            @Override
            public String getOpPackageName() {
                return ownerPackage;
            }
        };
    }

    /**
     * DisplayManagerService 校验「packageName 必须匹配调用方 uid」，
     * 所以包名不能写死：shell(2000) → com.android.shell，其它 uid → 该 uid 的包名。
     */
    private static String ownerPackage(Context base) {
        int uid = android.os.Process.myUid();
        if (uid == 2000) {
            return "com.android.shell";
        }
        try {
            String[] packages = base.getPackageManager().getPackagesForUid(uid);
            if (packages != null && packages.length > 0 && packages[0] != null) {
                return packages[0];
            }
        } catch (Throwable error) {
            emit("PROBE=ownerPackage failed " + causeChain(error));
        }
        return "com.android.shell";
    }

    /** 建屏后抓一帧验证「真能出画」，并落盘到 /data/local/tmp 供宿主侧检查。 */
    private static void selfTestFrame() {
        try {
            long deadline = System.currentTimeMillis() + 20_000L;
            Image image = null;
            while (System.currentTimeMillis() < deadline && image == null) {
                image = imageReader.acquireLatestImage();
                if (image == null) {
                    Thread.sleep(200L);
                }
            }
            if (image == null) {
                emit("PROBE=frame none");
                return;
            }
            try {
                android.media.Image.Plane plane = image.getPlanes()[0];
                java.nio.ByteBuffer buffer = plane.getBuffer();
                int pixelStride = plane.getPixelStride();
                int rowStride = plane.getRowStride();
                int w = image.getWidth();
                int h = image.getHeight();
                int rowPadding = rowStride - pixelStride * w;
                int bitmapWidth = w + rowPadding / pixelStride;
                Bitmap bitmap = Bitmap.createBitmap(bitmapWidth, h, Bitmap.Config.ARGB_8888);
                bitmap.copyPixelsFromBuffer(buffer);
                Bitmap cropped = bitmapWidth != w
                        ? Bitmap.createBitmap(bitmap, 0, 0, w, h) : bitmap;
                java.io.FileOutputStream out =
                        new java.io.FileOutputStream("/data/local/tmp/vdhost_frame.png");
                cropped.compress(Bitmap.CompressFormat.PNG, 100, out);
                out.close();
                emit("FRAME_OK=1 " + w + "x" + h);
            } finally {
                image.close();
            }
        } catch (Throwable error) {
            emit("PROBE=frame failed " + causeChain(error));
        }
    }

    /**
     * 帧服务：`LocalServerSocket("oas.vdhost.frame")`，客户端发一行 `FRAME` 就回
     * 「4 字节长度 + PNG」。宿主进程持有 ImageReader，App 通过本地 socket 取帧，
     * 这样「后台也能抓帧」且主屏完全看不到。
     */
    private static void startFrameServer() {
        Thread thread = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    android.net.LocalServerSocket server =
                            new android.net.LocalServerSocket("oas.vdhost.frame");
                    emit("PROBE=frame server listening");
                    while (true) {
                        android.net.LocalSocket client = server.accept();
                        try {
                            handleClient(client);
                        } catch (Throwable error) {
                            emit("PROBE=frame client error " + causeChain(error));
                        } finally {
                            try {
                                client.close();
                            } catch (Throwable ignored) {
                            }
                        }
                    }
                } catch (Throwable error) {
                    emit("PROBE=frame server failed " + causeChain(error));
                }
            }
        }, "vdhost-frame-server");
        thread.setDaemon(true);
        thread.start();
    }

    private static void handleClient(android.net.LocalSocket client) throws Exception {
        java.io.InputStream in = client.getInputStream();
        java.io.OutputStream out = client.getOutputStream();
        // 读一行命令
        StringBuilder command = new StringBuilder();
        int ch;
        while ((ch = in.read()) != -1 && ch != '\n') {
            command.append((char) ch);
        }
        byte[] png = readFramePng();
        if (png == null) {
            out.write(new byte[]{0, 0, 0, 0});
        } else {
            int length = png.length;
            out.write(new byte[]{
                    (byte) (length >>> 24), (byte) (length >>> 16),
                    (byte) (length >>> 8), (byte) length});
            out.write(png);
        }
        out.flush();
    }

    private static byte[] readFramePng() {
        Image image = null;
        long deadline = System.currentTimeMillis() + 3_000L;
        while (System.currentTimeMillis() < deadline && image == null) {
            image = imageReader.acquireLatestImage();
            if (image == null) {
                try {
                    Thread.sleep(40L);
                } catch (InterruptedException ignored) {
                }
            }
        }
        if (image == null) {
            return null;
        }
        try {
            android.media.Image.Plane plane = image.getPlanes()[0];
            java.nio.ByteBuffer buffer = plane.getBuffer();
            int pixelStride = plane.getPixelStride();
            int rowStride = plane.getRowStride();
            int w = image.getWidth();
            int h = image.getHeight();
            int rowPadding = rowStride - pixelStride * w;
            int bitmapWidth = w + rowPadding / pixelStride;
            Bitmap bitmap = Bitmap.createBitmap(bitmapWidth, h, Bitmap.Config.ARGB_8888);
            bitmap.copyPixelsFromBuffer(buffer);
            Bitmap cropped = bitmapWidth != w ? Bitmap.createBitmap(bitmap, 0, 0, w, h) : bitmap;
            java.io.ByteArrayOutputStream stream = new java.io.ByteArrayOutputStream();
            cropped.compress(Bitmap.CompressFormat.PNG, 100, stream);
            return stream.toByteArray();
        } finally {
            image.close();
        }
    }

    private static int parseInt(String raw, int fallback) {
        try {
            return Integer.parseInt(raw.trim());
        } catch (Exception ignored) {
            return fallback;
        }
    }
}
