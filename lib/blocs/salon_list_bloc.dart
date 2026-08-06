import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:salon_booking/api/entities/common.dart';
import 'package:salon_booking/core/base_bloc.dart';
import 'package:salon_booking/core/contracts/salon_list_contract.dart';
import 'package:salon_booking/core/screen_state.dart';
import 'package:salon_booking/core/view_actions.dart';
import 'package:salon_booking/services/salon_service.dart';

class SalonListBloc extends BaseBloc<SalonListEvent, SalonListData> {
  final SalonService _salonService;

  static SalonListData get initState =>
      const SalonListData(state: ScreenState.loading);

  SalonListBloc(this._salonService) : super(initState) {
    on<InitSalonListEvent>(_onInit);
    on<RetrySalonListEvent>(_onRetry);
    on<SearchSalonsEvent>(_onSearch);
    on<FilterSalonsByTagEvent>(_onFilterByTag);
    on<OpenSalonDetailsEvent>(_onOpenDetails);
    on<UpdateSalonListStateEvent>((event, emit) => emit(event.state));
  }

  void _onInit(InitSalonListEvent event, Emitter<SalonListData> emit) {
    _loadSalons();
  }

  void _onRetry(RetrySalonListEvent event, Emitter<SalonListData> emit) {
    emit(state.copyWith(state: ScreenState.loading, clearError: true));
    _loadSalons(query: state.query, tag: state.selectedTag);
  }

  void _onSearch(SearchSalonsEvent event, Emitter<SalonListData> emit) {
    emit(state.copyWith(query: event.query, state: ScreenState.loading));
    _loadSalons(query: event.query, tag: state.selectedTag);
  }

  void _onFilterByTag(
    FilterSalonsByTagEvent event,
    Emitter<SalonListData> emit,
  ) {
    emit(
      state.copyWith(
        selectedTag: event.tag,
        clearTag: event.tag == null,
        state: ScreenState.loading,
      ),
    );
    _loadSalons(query: state.query, tag: event.tag);
  }

  void _onOpenDetails(
    OpenSalonDetailsEvent event,
    Emitter<SalonListData> emit,
  ) {
    dispatchViewEvent(
      NavigateScreen(SalonListTarget.salonDetails, data: event.salonId),
    );
  }

  void _loadSalons({String? query, String? tag}) {
    _salonService.fetchSalons(query: query, tag: tag).then((response) {
      if (response.isSuccess && response.data != null) {
        add(
          UpdateSalonListStateEvent(
            state.copyWith(
              state: ScreenState.content,
              salons: response.data!,
              clearError: true,
            ),
          ),
        );
      } else {
        final message = response.exception is DisplayableError
            ? (response.exception as DisplayableError).errorMessage
            : 'Something went wrong';
        add(
          UpdateSalonListStateEvent(
            state.copyWith(state: ScreenState.error, errorMessage: message),
          ),
        );
      }
    });
  }
}
