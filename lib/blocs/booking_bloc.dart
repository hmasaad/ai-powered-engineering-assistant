import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:salon_booking/api/entities/common.dart';
import 'package:salon_booking/api/entities/salon.dart';
import 'package:salon_booking/core/base_bloc.dart';
import 'package:salon_booking/core/contracts/booking_contract.dart';
import 'package:salon_booking/core/screen_state.dart';
import 'package:salon_booking/core/view_actions.dart';
import 'package:salon_booking/services/salon_service.dart';

class BookingBloc extends BaseBloc<BookingEvent, BookingData> {
  final SalonService _salonService;
  final BookingService _bookingService;

  BookingBloc({
    required SalonService salonService,
    required BookingService bookingService,
    required Salon salon,
    required SalonServiceItem service,
  })  : _salonService = salonService,
        _bookingService = bookingService,
        super(
          BookingData(
            state: ScreenState.loading,
            submitState: ScreenState.content,
            salon: salon,
            service: service,
            selectedDay: DateTime.now(),
          ),
        ) {
    on<InitBookingEvent>(_onInit);
    on<SelectStylistEvent>(_onSelectStylist);
    on<SelectDayEvent>(_onSelectDay);
    on<SelectTimeSlotEvent>(_onSelectSlot);
    on<ConfirmBookingEvent>(_onConfirm);
    on<UpdateBookingStateEvent>((event, emit) => emit(event.state));
  }

  void _onInit(InitBookingEvent event, Emitter<BookingData> emit) {
    _loadStylists();
  }

  void _onSelectStylist(
    SelectStylistEvent event,
    Emitter<BookingData> emit,
  ) {
    emit(
      state.copyWith(
        selectedStylist: event.stylist,
        clearSlot: true,
        state: ScreenState.loading,
      ),
    );
    _loadSlots(stylistId: event.stylist.id, day: state.selectedDay);
  }

  void _onSelectDay(SelectDayEvent event, Emitter<BookingData> emit) {
    emit(
      state.copyWith(
        selectedDay: event.day,
        clearSlot: true,
        state: ScreenState.loading,
      ),
    );
    final stylist = state.selectedStylist;
    if (stylist != null) {
      _loadSlots(stylistId: stylist.id, day: event.day);
    } else {
      emit(state.copyWith(state: ScreenState.content, timeSlots: const []));
    }
  }

  void _onSelectSlot(SelectTimeSlotEvent event, Emitter<BookingData> emit) {
    if (!event.slot.isAvailable) return;
    emit(state.copyWith(selectedSlot: event.slot));
  }

  Future<void> _onConfirm(
    ConfirmBookingEvent event,
    Emitter<BookingData> emit,
  ) async {
    final stylist = state.selectedStylist;
    final slot = state.selectedSlot;
    if (stylist == null || slot == null) {
      emit(
        state.copyWith(
          submitError: 'Select a stylist and time slot',
        ),
      );
      return;
    }

    emit(
      state.copyWith(
        submitState: ScreenState.loading,
        clearSubmitError: true,
      ),
    );

    final response = await _bookingService.createBooking(
      salonId: state.salon.id,
      salonName: state.salon.name,
      serviceId: state.service.id,
      serviceName: state.service.name,
      stylistId: stylist.id,
      stylistName: stylist.name,
      start: slot.start,
      price: state.service.price,
    );

    if (response.isSuccess && response.data != null) {
      emit(state.copyWith(submitState: ScreenState.content));
      dispatchViewEvent(
        NavigateScreen(BookingTarget.bookingConfirmed, data: response.data),
      );
    } else {
      final message = response.exception is DisplayableError
          ? (response.exception as DisplayableError).errorMessage
          : 'Booking failed';
      emit(
        state.copyWith(
          submitState: ScreenState.error,
          submitError: message,
        ),
      );
    }
  }

  Future<void> _loadStylists() async {
    final response = await _salonService.fetchStylists(
      state.salon.id,
      serviceId: state.service.id,
    );
    if (!response.isSuccess || response.data == null) {
      add(
        UpdateBookingStateEvent(
          state.copyWith(
            state: ScreenState.error,
            errorMessage: 'Failed to load stylists',
          ),
        ),
      );
      return;
    }

    final stylists = response.data!;
    final first = stylists.isNotEmpty ? stylists.first : null;
    add(
      UpdateBookingStateEvent(
        state.copyWith(
          state: first == null ? ScreenState.content : ScreenState.loading,
          stylists: stylists,
          selectedStylist: first,
          clearError: true,
        ),
      ),
    );
    if (first != null) {
      _loadSlots(stylistId: first.id, day: state.selectedDay);
    }
  }

  Future<void> _loadSlots({
    required String stylistId,
    required DateTime day,
  }) async {
    final response = await _salonService.fetchTimeSlots(
      stylistId: stylistId,
      day: day,
    );
    if (!response.isSuccess || response.data == null) {
      add(
        UpdateBookingStateEvent(
          state.copyWith(
            state: ScreenState.error,
            errorMessage: 'Failed to load time slots',
          ),
        ),
      );
      return;
    }
    add(
      UpdateBookingStateEvent(
        state.copyWith(
          state: ScreenState.content,
          timeSlots: response.data!,
          clearError: true,
        ),
      ),
    );
  }
}
