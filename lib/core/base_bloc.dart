import 'dart:async';

import 'package:bloc/bloc.dart';
import 'package:flutter/widgets.dart';
import 'package:rxdart/rxdart.dart';

import 'view_actions.dart';

abstract class BaseBloc<Event, State> extends Bloc<Event, State> {
  final PublishSubject<ViewAction> _sideEffects = PublishSubject();

  Stream<ViewAction> get viewActions => _sideEffects.stream;

  final List<StreamSubscription> _subscriptions = [];

  BaseBloc(super.state);

  @protected
  void dispatchViewEvent(ViewAction target) {
    _sideEffects.add(target);
  }

  @override
  Future<void> close() {
    for (final subscription in _subscriptions) {
      subscription.cancel();
    }
    _sideEffects.close();
    return super.close();
  }
}

extension StreamLifecycle on StreamSubscription {
  void bindToLifecycle(BaseBloc<dynamic, dynamic> bloc) {
    bloc._subscriptions.add(this);
  }
}
