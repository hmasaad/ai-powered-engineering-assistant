import 'package:equatable/equatable.dart';
import 'package:salon_booking/api/entities/salon.dart';
import 'package:salon_booking/core/screen_state.dart';

class SalonListData extends Equatable {
  final ScreenState state;
  final List<Salon> salons;
  final String query;
  final String? errorMessage;

  const SalonListData({
    required this.state,
    this.salons = const [],
    this.query = '',
    this.errorMessage,
  });

  SalonListData copyWith({
    ScreenState? state,
    List<Salon>? salons,
    String? query,
    String? errorMessage,
    bool clearError = false,
  }) {
    return SalonListData(
      state: state ?? this.state,
      salons: salons ?? this.salons,
      query: query ?? this.query,
      errorMessage: clearError ? null : (errorMessage ?? this.errorMessage),
    );
  }

  @override
  List<Object?> get props => [state, salons, query, errorMessage];
}

abstract class SalonListEvent {}

class InitSalonListEvent extends SalonListEvent {}

class RetrySalonListEvent extends SalonListEvent {}

class SearchSalonsEvent extends SalonListEvent {
  final String query;

  SearchSalonsEvent(this.query);
}

class OpenSalonDetailsEvent extends SalonListEvent {
  final String salonId;

  OpenSalonDetailsEvent(this.salonId);
}

class UpdateSalonListStateEvent extends SalonListEvent {
  final SalonListData state;

  UpdateSalonListStateEvent(this.state);
}

class SalonListTarget {
  static const salonDetails = 'salon_details';
}
