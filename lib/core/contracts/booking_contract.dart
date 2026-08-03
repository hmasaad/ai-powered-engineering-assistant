import 'package:equatable/equatable.dart';
import 'package:salon_booking/api/entities/salon.dart';
import 'package:salon_booking/core/screen_state.dart';

class BookingData extends Equatable {
  final ScreenState state;
  final ScreenState submitState;
  final Salon salon;
  final SalonServiceItem service;
  final List<Stylist> stylists;
  final Stylist? selectedStylist;
  final DateTime selectedDay;
  final List<TimeSlot> timeSlots;
  final TimeSlot? selectedSlot;
  final String? errorMessage;
  final String? submitError;

  const BookingData({
    required this.state,
    required this.submitState,
    required this.salon,
    required this.service,
    this.stylists = const [],
    this.selectedStylist,
    required this.selectedDay,
    this.timeSlots = const [],
    this.selectedSlot,
    this.errorMessage,
    this.submitError,
  });

  BookingData copyWith({
    ScreenState? state,
    ScreenState? submitState,
    List<Stylist>? stylists,
    Stylist? selectedStylist,
    bool clearStylist = false,
    DateTime? selectedDay,
    List<TimeSlot>? timeSlots,
    TimeSlot? selectedSlot,
    bool clearSlot = false,
    String? errorMessage,
    bool clearError = false,
    String? submitError,
    bool clearSubmitError = false,
  }) {
    return BookingData(
      state: state ?? this.state,
      submitState: submitState ?? this.submitState,
      salon: salon,
      service: service,
      stylists: stylists ?? this.stylists,
      selectedStylist:
          clearStylist ? null : (selectedStylist ?? this.selectedStylist),
      selectedDay: selectedDay ?? this.selectedDay,
      timeSlots: timeSlots ?? this.timeSlots,
      selectedSlot: clearSlot ? null : (selectedSlot ?? this.selectedSlot),
      errorMessage: clearError ? null : (errorMessage ?? this.errorMessage),
      submitError:
          clearSubmitError ? null : (submitError ?? this.submitError),
    );
  }

  @override
  List<Object?> get props => [
        state,
        submitState,
        salon,
        service,
        stylists,
        selectedStylist,
        selectedDay,
        timeSlots,
        selectedSlot,
        errorMessage,
        submitError,
      ];
}

abstract class BookingEvent {}

class InitBookingEvent extends BookingEvent {}

class SelectStylistEvent extends BookingEvent {
  final Stylist stylist;

  SelectStylistEvent(this.stylist);
}

class SelectDayEvent extends BookingEvent {
  final DateTime day;

  SelectDayEvent(this.day);
}

class SelectTimeSlotEvent extends BookingEvent {
  final TimeSlot slot;

  SelectTimeSlotEvent(this.slot);
}

class ConfirmBookingEvent extends BookingEvent {}

class UpdateBookingStateEvent extends BookingEvent {
  final BookingData state;

  UpdateBookingStateEvent(this.state);
}

class BookingTarget {
  static const bookingConfirmed = 'booking_confirmed';
}
