package com.oas.oasx;

import android.hardware.display.VirtualDisplay;
import android.hardware.display.VirtualDisplayConfig;
import android.os.Build;
import android.os.IBinder;
import android.os.SystemClock;
import android.view.Display;
import android.view.Surface;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * Android 14+ 的 VDM 后台虚拟屏。
 *
 * 路径与 MAA-Meow VdmDisplayController 一致：
 * cmd companiondevice associate -> VirtualDeviceManager.createVirtualDevice
 * -> VirtualDevice.createVirtualDisplay -> 独立 displayGroup。
 *
 * 该路径由 shell user-service 执行，不需要 MediaProjection，也不会弹录屏授权。
 */
final class VdmDisplay {
    private static final String TAG = "OasVdmDisplay";
    private static final String SHELL_PACKAGE = "com.android.shell";
    private static final String STREAMING_PROFILE =
            "android.app.role.COMPANION_DEVICE_APP_STREAMING";
    private static final String ADDRESS = "02:4F:41:53:00:01";
    private static final int USER_ID = 0;
    private static final int LOCK_STATE_ALWAYS_UNLOCKED = 1;
    private static final long ASSOCIATION_TIMEOUT_MS = 17_000L;
    private static final long DISPLAY_TIMEOUT_MS = 1_500L;

    private static final int FLAG_SUPPORTS_TOUCH = 1 << 6;
    private static final int FLAG_DESTROY_CONTENT_ON_REMOVAL = 1 << 8;
    private static final int FLAG_TRUSTED = 1 << 10;
    private static final int FLAG_TOUCH_FEEDBACK_DISABLED = 1 << 13;
    private static final int FLAG_OWN_FOCUS = 1 << 14;
    private static final int FLAG_STEAL_TOP_FOCUS_DISABLED = 1 << 16;

    private static final int BASE_FLAGS =
            FLAG_SUPPORTS_TOUCH | FLAG_DESTROY_CONTENT_ON_REMOVAL | FLAG_TOUCH_FEEDBACK_DISABLED;
    private static final int TRUSTED_FLAGS =
            FLAG_TRUSTED | FLAG_OWN_FOCUS | FLAG_STEAL_TOP_FOCUS_DISABLED;

    static final class Session {
        final Object device;
        final VirtualDisplay display;
        final int flags;

        Session(Object device, VirtualDisplay display, int flags) {
            this.device = device;
            this.display = display;
            this.flags = flags;
        }

        int displayId() {
            return display.getDisplay().getDisplayId();
        }

        void close() {
            try {
                display.setSurface(null);
            } catch (Throwable ignored) {
            }
            try {
                display.release();
            } catch (Throwable ignored) {
            }
            try {
                device.getClass().getMethod("close").invoke(device);
            } catch (Throwable ignored) {
            }
        }
    }

    private VdmDisplay() {
    }

    static Session start(int width, int height, int dpi, Surface surface) throws Exception {
        if (Build.VERSION.SDK_INT < 34) {
            throw new UnsupportedOperationException("VDM requires Android 14+");
        }
        int associationId = ensureAssociation();
        Object manager = createVirtualDeviceManager();
        Constructor<?> paramsBuilderCtor =
                Class.forName("android.companion.virtual.VirtualDeviceParams$Builder")
                        .getConstructor();
        Method setLockState = paramsBuilderCtor.getDeclaringClass()
                .getMethod("setLockState", int.class);
        Method buildParams = paramsBuilderCtor.getDeclaringClass().getMethod("build");
        Class<?> paramsClass = Class.forName("android.companion.virtual.VirtualDeviceParams");
        Method createVirtualDevice = manager.getClass()
                .getMethod("createVirtualDevice", int.class, paramsClass);

        Throwable lastFailure = null;
        for (Tier tier : tiers()) {
            Object device = null;
            try {
                Object builder = paramsBuilderCtor.newInstance();
                if (tier.alwaysUnlocked) {
                    setLockState.invoke(builder, LOCK_STATE_ALWAYS_UNLOCKED);
                }
                Object params = buildParams.invoke(builder);
                device = unwrapInvocation(
                        createVirtualDevice.invoke(manager, associationId, params), "createVirtualDevice");
                if (device == null) {
                    throw new IllegalStateException("VDM returned no virtual device");
                }

                int flags = tier.flags();
                VirtualDisplayConfig config = new VirtualDisplayConfig.Builder(
                        "oas-frame-binder", width, height, dpi)
                        .setSurface(surface)
                        .setFlags(flags)
                        .build();
                Method createDisplay = device.getClass().getMethod(
                        "createVirtualDisplay",
                        VirtualDisplayConfig.class,
                        java.util.concurrent.Executor.class,
                        VirtualDisplay.Callback.class);
                VirtualDisplay display = (VirtualDisplay) unwrapInvocation(
                        createDisplay.invoke(device, config, null, null),
                        "VirtualDevice.createVirtualDisplay");
                if (display == null) {
                    throw new IllegalStateException("VDM returned no display");
                }
                awaitDisplayReady(display.getDisplay(), tier, flags);
                android.util.Log.i(
                        TAG,
                        "VDM ready id=" + display.getDisplay().getDisplayId()
                                + " flags=0x" + Integer.toHexString(flags)
                                + " group=" + displayGroupId(display.getDisplay().getDisplayId())
                                + " tier=" + tier);
                return new Session(device, display, flags);
            } catch (Throwable failure) {
                lastFailure = unwrap(failure);
                android.util.Log.w(TAG, "VDM tier rejected " + tier + ": " + lastFailure);
                if (device != null) {
                    try {
                        device.getClass().getMethod("close").invoke(device);
                    } catch (Throwable ignored) {
                    }
                }
            }
        }
        throw new IllegalStateException("all VDM tiers failed", lastFailure);
    }

    private static Object createVirtualDeviceManager() throws Exception {
        Object iface = serviceInterface(
                "virtualdevice",
                "android.companion.virtual.IVirtualDeviceManager");
        Class<?> aidlClass = Class.forName("android.companion.virtual.IVirtualDeviceManager");
        Constructor<?> ctor = Class.forName("android.companion.virtual.VirtualDeviceManager")
                .getDeclaredConstructor(aidlClass, android.content.Context.class);
        ctor.setAccessible(true);
        return ctor.newInstance(iface, ShellContext.get());
    }

    private static int ensureAssociation() throws Exception {
        Integer existing = findAssociation();
        if (existing != null) {
            return existing;
        }

        String[] commands = Build.VERSION.SDK_INT >= 35
                ? new String[]{
                        "cmd companiondevice associate " + USER_ID + " " + SHELL_PACKAGE + " " + ADDRESS + " " + STREAMING_PROFILE + " false",
                        "cmd companiondevice associate " + USER_ID + " " + SHELL_PACKAGE + " " + ADDRESS + " " + STREAMING_PROFILE,
                        "cmd companiondevice associate " + USER_ID + " " + SHELL_PACKAGE + " " + ADDRESS,
                }
                : new String[]{
                        "cmd companiondevice associate " + USER_ID + " " + SHELL_PACKAGE + " " + ADDRESS,
                };
        int code = -1;
        for (String command : commands) {
            code = shellExec(command);
            if (code == 0) {
                break;
            }
            android.util.Log.w(TAG, "companiondevice associate attempt failed code=" + code + " cmd=" + command);
        }
        if (code != 0) {
            throw new IllegalStateException("companiondevice associate failed code=" + code);
        }

        long deadline = SystemClock.elapsedRealtime() + ASSOCIATION_TIMEOUT_MS;
        do {
            Integer id = findAssociation();
            if (id != null) {
                android.util.Log.i(TAG, "association ready id=" + id + " address=" + ADDRESS);
                return id;
            }
            SystemClock.sleep(100L);
        } while (SystemClock.elapsedRealtime() < deadline);
        throw new IllegalStateException("Companion association was not created");
    }

    private static Integer findAssociation() throws Exception {
        Object manager = serviceInterface(
                "companiondevice",
                "android.companion.ICompanionDeviceManager");
        Method getAssociations = manager.getClass()
                .getMethod("getAssociations", String.class, int.class);
        Object result = unwrapInvocation(
                getAssociations.invoke(manager, SHELL_PACKAGE, USER_ID),
                "getAssociations");
        if (!(result instanceof List<?>)) {
            return null;
        }
        for (Object association : (List<?>) result) {
            Method macMethod = association.getClass().getMethod("getDeviceMacAddress");
            Object mac = macMethod.invoke(association);
            if (mac != null && ADDRESS.equalsIgnoreCase(String.valueOf(mac))) {
                return (Integer) association.getClass().getMethod("getId").invoke(association);
            }
        }
        return null;
    }

    private static Object serviceInterface(String serviceName, String interfaceName)
            throws Exception {
        Method getService = Class.forName("android.os.ServiceManager")
                .getDeclaredMethod("getService", String.class);
        getService.setAccessible(true);
        IBinder binder = (IBinder) getService.invoke(null, serviceName);
        Class<?> stub = Class.forName(interfaceName + "$Stub");
        Method asInterface = stub.getMethod("asInterface", IBinder.class);
        return asInterface.invoke(null, binder);
    }

    private static int shellExec(String command) throws Exception {
        Process process = new ProcessBuilder("sh", "-c", command)
                .redirectErrorStream(true)
                .start();
        if (!process.waitFor(20, TimeUnit.SECONDS)) {
            process.destroy();
            return -1;
        }
        return process.exitValue();
    }

    private static void awaitDisplayReady(Display display, Tier tier, int flags) {
        long deadline = SystemClock.elapsedRealtime() + DISPLAY_TIMEOUT_MS;
        do {
            int group = displayGroupId(display.getDisplayId());
            if (group > 0 && display.getState() == Display.STATE_ON) {
                return;
            }
            SystemClock.sleep(30L);
        } while (SystemClock.elapsedRealtime() < deadline);
        throw new IllegalStateException(
                "VDM display not ready: id=" + display.getDisplayId()
                        + " group=" + displayGroupId(display.getDisplayId())
                        + " state=" + display.getState()
                        + " tier=" + tier
                        + " flags=0x" + Integer.toHexString(flags));
    }

    private static int displayGroupId(int displayId) {
        try {
            Class<?> globalClass = Class.forName("android.hardware.display.DisplayManagerGlobal");
            Object global = globalClass.getDeclaredMethod("getInstance").invoke(null);
            Object info = globalClass.getMethod("getDisplayInfo", int.class)
                    .invoke(global, displayId);
            if (info == null) return -1;
            Field field = info.getClass().getDeclaredField("displayGroupId");
            field.setAccessible(true);
            return field.getInt(info);
        } catch (Throwable ignored) {
            return -1;
        }
    }

    private static Object unwrapInvocation(Object result, String label) throws Exception {
        return result;
    }

    private static Throwable unwrap(Throwable failure) {
        while (failure instanceof InvocationTargetException
                && ((InvocationTargetException) failure).getTargetException() != null) {
            failure = ((InvocationTargetException) failure).getTargetException();
        }
        return failure;
    }

    private static final class Tier {
        final boolean alwaysUnlocked;
        final boolean trusted;

        Tier(boolean alwaysUnlocked, boolean trusted) {
            this.alwaysUnlocked = alwaysUnlocked;
            this.trusted = trusted;
        }

        int flags() {
            return BASE_FLAGS | (trusted ? TRUSTED_FLAGS : 0);
        }

        @Override
        public String toString() {
            return "alwaysUnlocked=" + alwaysUnlocked + ",trusted=" + trusted;
        }
    }

    private static Tier[] tiers() {
        return new Tier[]{
                new Tier(true, true),
                new Tier(true, false),
                new Tier(false, true),
                new Tier(false, false),
        };
    }
}
