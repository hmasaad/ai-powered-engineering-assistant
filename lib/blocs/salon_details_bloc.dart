import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:salon_booking/api/entities/common.dart';
import 'package:salon_booking/core/base_bloc.dart';
import 'package:salon_booking/core/contracts/salon_details_contract.dart';
import 'package:salon_booking/core/screen_state.dart';
import 'package:salon_booking/core/view_actions.dart';
import 'package:salon_booking/services/salon_service.dart';

class SalonDetailsBloc extends BaseBloc<SalonDetailsEvent, SalonDetailsData> {
  final SalonService _salonService;

  static SalonDetailsData get initState =>
      const SalonDetailsData(state: ScreenState.loading);

  SalonDetailsBloc(this._salonService) : super(initState) {
    on<InitSalonDetailsEvent>(_onInit);
    on<RetrySalonDetailsEvent>(_onRetry);
    on<BookServiceEvent>(_onBookService);
    on<UpdateSalonDetailsStateEvent>((event, emit) => emit(event.state));
  }

  void _onInit(InitSalonDetailsEvent event, Emitter<SalonDetailsData> emit) {
    _load(event.salonId);
  }

  void _onRetry(RetrySalonDetailsEvent event, Emitter<SalonDetailsData> emit) {
    emit(state.copyWith(state: ScreenState.loading, clearError: true));
    _load(event.salonId);
  }

  void _onBookService(
    BookServiceEvent event,
    Emitter<SalonDetailsData> emit,
  ) {
    if (state.salon == null) return;
    dispatchViewEvent(
      NavigateScreen(
        SalonDetailsTarget.booking,
        data: {
          'salon': state.salon!,
          'service': event.service,
        },
      ),
    );
  }

  Future<void> _load(String salonId) async {
    final salonResponse = await _salonService.fetchSalon(salonId);
    if (!salonResponse.isSuccess || salonResponse.data == null) {
      final message = salonResponse.exception is DisplayableError
          ? (salonResponse.exception as DisplayableError).errorMessage
          : 'Failed to load salon';
      add(
        UpdateSalonDetailsStateEvent(
          state.copyWith(state: ScreenState.error, errorMessage: message),
        ),
      );
      return;
    }

    final servicesResponse = await _salonService.fetchServices(salonId);
    final stylistsResponse = await _salonService.fetchStylists(salonId);

    if (!servicesResponse.isSuccess || !stylistsResponse.isSuccess) {
      add(
        UpdateSalonDetailsStateEvent(
          state.copyWith(
            state: ScreenState.error,
            errorMessage: 'Failed to load salon details',
          ),
        ),
      );
      return;
    }

    add(
      UpdateSalonDetailsStateEvent(
        state.copyWith(
          state: ScreenState.content,
          salon: salonResponse.data,
          services: servicesResponse.data ?? [],
          stylists: stylistsResponse.data ?? [],
          clearError: true,
        ),
      ),
    );
  }
}
