import 'package:salon_booking/api/booking_api.dart';
import 'package:salon_booking/api/entities/common.dart';
import 'package:salon_booking/api/entities/salon.dart';
import 'package:salon_booking/api/salon_api.dart';

class SalonService {
  final SalonApi _salonApi;

  SalonService(this._salonApi);

  Future<ResponseEntity<List<Salon>>> fetchSalons({String? query}) {
    return _salonApi.getSalons(query: query);
  }

  Future<ResponseEntity<Salon>> fetchSalon(String salonId) {
    return _salonApi.getSalonById(salonId);
  }

  Future<ResponseEntity<List<SalonServiceItem>>> fetchServices(String salonId) {
    return _salonApi.getServices(salonId);
  }

  Future<ResponseEntity<List<Stylist>>> fetchStylists(
    String salonId, {
    String? serviceId,
  }) {
    return _salonApi.getStylists(salonId, serviceId: serviceId);
  }

  Future<ResponseEntity<List<TimeSlot>>> fetchTimeSlots({
    required String stylistId,
    required DateTime day,
  }) {
    return _salonApi.getTimeSlots(stylistId: stylistId, day: day);
  }
}

class BookingService {
  final BookingApi _bookingApi;

  BookingService(this._bookingApi);

  Future<ResponseEntity<List<Booking>>> fetchBookings() {
    return _bookingApi.getBookings();
  }

  Future<ResponseEntity<Booking>> createBooking({
    required String salonId,
    required String salonName,
    required String serviceId,
    required String serviceName,
    required String stylistId,
    required String stylistName,
    required DateTime start,
    required double price,
  }) {
    return _bookingApi.createBooking(
      salonId: salonId,
      salonName: salonName,
      serviceId: serviceId,
      serviceName: serviceName,
      stylistId: stylistId,
      stylistName: stylistName,
      start: start,
      price: price,
      );
  }

  Future<ResponseEntity<bool>> cancelBooking(String bookingId) {
    return _bookingApi.cancelBooking(bookingId);
  }
}
