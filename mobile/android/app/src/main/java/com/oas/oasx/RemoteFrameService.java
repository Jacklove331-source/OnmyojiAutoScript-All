package com.oas.oasx;

import android.os.Binder;
import android.os.Parcel;
import android.os.RemoteException;

/**
 * Shizuku 直接实例化的 user-service Binder。
 *
 * onTransact 放在 Java 壳里，是因为当前 Kotlin 编译器无法识别 Android SDK 中
 * Binder.onTransact 的 protected 覆盖签名；业务实现仍在 RemoteFrameCore.kt。
 */
public final class RemoteFrameService extends Binder {

    public static final String DESCRIPTOR = RemoteFrameCore.DESCRIPTOR;
    public static final int TRANSACTION_START = RemoteFrameCore.TRANSACTION_START;
    public static final int TRANSACTION_CAPTURE = RemoteFrameCore.TRANSACTION_CAPTURE;
    public static final int TRANSACTION_STOP = RemoteFrameCore.TRANSACTION_STOP;
    public static final int TRANSACTION_STATUS = RemoteFrameCore.TRANSACTION_STATUS;
    public static final int TRANSACTION_PING = RemoteFrameCore.TRANSACTION_PING;
    public static final int TRANSACTION_TAP = RemoteFrameCore.TRANSACTION_TAP;
    public static final int TRANSACTION_SWIPE = RemoteFrameCore.TRANSACTION_SWIPE;
    public static final int TRANSACTION_KEYEVENT = RemoteFrameCore.TRANSACTION_KEYEVENT;

    private final RemoteFrameCore core = new RemoteFrameCore();

    @Override
    protected boolean onTransact(int code, Parcel data, Parcel reply, int flags)
            throws RemoteException {
        return core.onTransact(code, data, reply, flags);
    }
}
