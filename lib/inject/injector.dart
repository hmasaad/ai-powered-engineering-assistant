import 'package:get_it/get_it.dart';
import 'package:salon_booking/api/booking_api.dart';
import 'package:salon_booking/api/entities/salon.dart';
import 'package:salon_booking/api/salon_api.dart';
import 'package:salon_booking/blocs/booking_bloc.dart';
import 'package:salon_booking/blocs/my_bookings_bloc.dart';
import 'package:salon_booking/blocs/salon_details_bloc.dart';
import 'package:salon_booking/blocs/salon_list_bloc.dart';
import 'package:salon_booking/services/salon_service.dart';

final GetIt getIt = GetIt.instance;

abstract class Injector {
  static Future<void> setup() async {
    if (getIt.isRegistered<SalonApi>()) {
      await getIt.reset();
    }
    _configure();
  }

  static T resolve<T extends Object>() => getIt<T>();

  static void _configure() {
    getIt
      ..registerLazySingleton<SalonApi>(SalonApi.new)
      ..registerLazySingleton<BookingApi>(BookingApi.new)
      ..registerLazySingleton<SalonService>(
        () => SalonService(getIt<SalonApi>()),
      )
      ..registerLazySingleton<BookingService>(
        () => BookingService(getIt<BookingApi>()),
      )
      ..registerFactory<SalonListBloc>(
        () => SalonListBloc(getIt<SalonService>()),
      )
      ..registerFactory<SalonDetailsBloc>(
        () => SalonDetailsBloc(getIt<SalonService>()),
      )
      ..registerFactory<MyBookingsBloc>(
        () => MyBookingsBloc(getIt<BookingService>()),
      );
  }

  static BookingBloc createBookingBloc({
    required Salon salon,
    required SalonServiceItem service,
  }) {
    return BookingBloc(
      salonService: resolve<SalonService>(),
      bookingService: resolve<BookingService>(),
      salon: salon,
      service: service,
    );
  }
}
