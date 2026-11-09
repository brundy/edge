// CPU wake lock while sync work is in flight (Android only).
//
// The Edge Tracking foreground service keeps the process alive in the
// background, but not the CPU: with the screen off the phone suspends between
// BLE notifications, so an offload drain and the derive pass behind it crawl
// or stall until the screen comes back on. [CpuWake] mirrors a single "busy"
// flag to a native partial wake lock (CpuWake.kt). The native side times the
// lock out on its own, so this re-sends "busy" on [refreshEvery] while work is
// still going, and a dead or confused Dart side can never pin the CPU.

import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

class CpuWake {
  CpuWake({
    Future<void> Function(bool on)? send,
    this.refreshEvery = const Duration(minutes: 10),
    void Function(String)? log,
  }) : _send = send ?? _platformSend,
       _log = log;

  static const _ch = MethodChannel('openstrap/edge_tracking');

  static Future<void> _platformSend(bool on) async {
    if (!Platform.isAndroid) return;
    try {
      await _ch.invokeMethod('cpuWake', {'on': on});
    } catch (e) {
      debugPrint('[cpu-wake] ${on ? "acquire" : "release"} failed: $e');
    }
  }

  final Future<void> Function(bool on) _send;
  final Duration refreshEvery;
  final void Function(String)? _log;

  bool _held = false;
  Timer? _refresh;

  bool get held => _held;

  /// Mirror the current busy state. Cheap to call on every state change:
  /// only a transition reaches the platform.
  void set(bool busy) {
    if (busy == _held) return;
    _held = busy;
    _refresh?.cancel();
    _refresh = null;
    _log?.call('[cpu-wake] ${busy ? "holding CPU awake" : "released"}');
    unawaited(_send(busy));
    if (busy) {
      _refresh = Timer.periodic(refreshEvery, (_) => unawaited(_send(true)));
    }
  }

  void dispose() => set(false);
}
