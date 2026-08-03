import 'package:equatable/equatable.dart';
import 'package:salon_booking/api/entities/salon.dart';
import 'package:salon_booking/core/screen_state.dart';

class MyBookingsData extends Equatable {
  final ScreenState state;
  final List<Booking> bookings;
  final String? errorMessage;

  const MyBookingsData({
    required this.state,
    this.bookings = const [],
    this.errorMessage,
  });

  MyBookingsData copyWith({
    ScreenState? state,
    List<Booking>? bookings,
    String? errorMessage,
    bool clearError = false,
  }) {
    return MyBookingsData(
      state: state ?? this.state,
      bookings: bookings ?? this.bookings,
      errorMessage: clearError ? null : (errorMessage ?? this.errorMessage),
    );
  }

  @override
  List<Object?> get props => [state, bookings, errorMessage];
}

abstract class MyBookingsEvent {}

class InitMyBookingsEvent extends MyBookingsEvent {}

class RetryMyBookingsEvent extends MyBookingsEvent {}

class CancelBookingEvent extends MyBookingsEvent {
  final String bookingId;

  CancelBookingEvent(this.bookingId);
}

class UpdateMyBookingsStateEvent extends MyBookingsEvent {
  final MyBookingsData state;

  UpdateMyBookingsStateEvent(this.state);
}
