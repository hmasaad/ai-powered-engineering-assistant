import 'package:equatable/equatable.dart';
import 'package:salon_booking/api/entities/salon.dart';
import 'package:salon_booking/core/screen_state.dart';

class SalonDetailsData extends Equatable {
  final ScreenState state;
  final Salon? salon;
  final List<SalonServiceItem> services;
  final List<Stylist> stylists;
  final String? errorMessage;

  const SalonDetailsData({
    required this.state,
    this.salon,
    this.services = const [],
    this.stylists = const [],
    this.errorMessage,
  });

  SalonDetailsData copyWith({
    ScreenState? state,
    Salon? salon,
    List<SalonServiceItem>? services,
    List<Stylist>? stylists,
    String? errorMessage,
    bool clearError = false,
  }) {
    return SalonDetailsData(
      state: state ?? this.state,
      salon: salon ?? this.salon,
      services: services ?? this.services,
      stylists: stylists ?? this.stylists,
      errorMessage: clearError ? null : (errorMessage ?? this.errorMessage),
    );
  }

  @override
  List<Object?> get props => [state, salon, services, stylists, errorMessage];
}

abstract class SalonDetailsEvent {}

class InitSalonDetailsEvent extends SalonDetailsEvent {
  final String salonId;

  InitSalonDetailsEvent(this.salonId);
}

class RetrySalonDetailsEvent extends SalonDetailsEvent {
  final String salonId;

  RetrySalonDetailsEvent(this.salonId);
}

class BookServiceEvent extends SalonDetailsEvent {
  final SalonServiceItem service;

  BookServiceEvent(this.service);
}

class UpdateSalonDetailsStateEvent extends SalonDetailsEvent {
  final SalonDetailsData state;

  UpdateSalonDetailsStateEvent(this.state);
}

class SalonDetailsTarget {
  static const booking = 'booking';
}
