import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:salon_booking/api/entities/common.dart';
import 'package:salon_booking/core/base_bloc.dart';
import 'package:salon_booking/core/contracts/my_bookings_contract.dart';
import 'package:salon_booking/core/screen_state.dart';
import 'package:salon_booking/core/view_actions.dart';
import 'package:salon_booking/services/salon_service.dart';

class MyBookingsBloc extends BaseBloc<MyBookingsEvent, MyBookingsData> {
  final BookingService _bookingService;

  static MyBookingsData get initState =>
      const MyBookingsData(state: ScreenState.loading);

  MyBookingsBloc(this._bookingService) : super(initState) {
    on<InitMyBookingsEvent>(_onInit);
    on<RetryMyBookingsEvent>(_onRetry);
    on<CancelBookingEvent>(_onCancel);
    on<UpdateMyBookingsStateEvent>((event, emit) => emit(event.state));
  }

  void _onInit(InitMyBookingsEvent event, Emitter<MyBookingsData> emit) {
    _load();
  }

  void _onRetry(RetryMyBookingsEvent event, Emitter<MyBookingsData> emit) {
    emit(state.copyWith(state: ScreenState.loading, clearError: true));
    _load();
  }

  Future<void> _onCancel(
    CancelBookingEvent event,
    Emitter<MyBookingsData> emit,
  ) async {
    final response = await _bookingService.cancelBooking(event.bookingId);
    if (response.isSuccess) {
      dispatchViewEvent(DisplayMessage('Booking cancelled'));
      _load();
    } else {
      final message = response.exception is DisplayableError
          ? (response.exception as DisplayableError).errorMessage
          : 'Cancel failed';
      dispatchViewEvent(DisplayMessage(message));
    }
  }

  void _load() {
    _bookingService.fetchBookings().then((response) {
      if (response.isSuccess && response.data != null) {
        add(
          UpdateMyBookingsStateEvent(
            state.copyWith(
              state: ScreenState.content,
              bookings: response.data!,
              clearError: true,
            ),
          ),
        );
      } else {
        final message = response.exception is DisplayableError
            ? (response.exception as DisplayableError).errorMessage
            : 'Failed to load bookings';
        add(
          UpdateMyBookingsStateEvent(
            state.copyWith(state: ScreenState.error, errorMessage: message),
          ),
        );
      }
    });
  }
}
