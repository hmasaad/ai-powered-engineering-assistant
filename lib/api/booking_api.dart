import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:salon_booking/api/entities/common.dart';
import 'package:salon_booking/api/entities/salon.dart';

/// Sample booking API with in-memory mutations on top of asset seed data.
class BookingApi {
  final List<Booking> _bookings = [];
  bool _seeded = false;

  Future<void> _ensureSeeded() async {
    if (_seeded) return;
    await Future<void>.delayed(const Duration(milliseconds: 350));
    final raw = await rootBundle.loadString('assets/data/salon_booking.json');
    final data = jsonDecode(raw) as Map<String, dynamic>;
    _bookings.addAll(
      (data['bookings'] as List<dynamic>)
          .map((e) => Booking.fromJson(e as Map<String, dynamic>)),
    );
    _seeded = true;
  }

  Future<ResponseEntity<List<Booking>>> getBookings() async {
    try {
      await _ensureSeeded();
      final sorted = [..._bookings]
        ..sort((a, b) => a.start.compareTo(b.start));
      return ResponseEntity.success(sorted);
    } catch (e) {
      return ResponseEntity.failure(DisplayableError('Failed to load bookings'));
    }
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
  }) async {
    try {
      await _ensureSeeded();
      await Future<void>.delayed(const Duration(milliseconds: 500));
      final booking = Booking(
        id: 'book-${DateTime.now().millisecondsSinceEpoch}',
        salonId: salonId,
        salonName: salonName,
        serviceId: serviceId,
        serviceName: serviceName,
        stylistId: stylistId,
        stylistName: stylistName,
        start: start,
        price: price,
        status: 'confirmed',
      );
      _bookings.add(booking);
      return ResponseEntity.success(booking);
    } catch (e) {
      return ResponseEntity.failure(
        DisplayableError('Failed to create booking'),
      );
    }
  }

  Future<ResponseEntity<bool>> cancelBooking(String bookingId) async {
    try {
      await _ensureSeeded();
      final index = _bookings.indexWhere((b) => b.id == bookingId);
      if (index < 0) {
        return ResponseEntity.failure(DisplayableError('Booking not found'));
      }
      final existing = _bookings[index];
      _bookings[index] = Booking(
        id: existing.id,
        salonId: existing.salonId,
        salonName: existing.salonName,
        serviceId: existing.serviceId,
        serviceName: existing.serviceName,
        stylistId: existing.stylistId,
        stylistName: existing.stylistName,
        start: existing.start,
        price: existing.price,
        status: 'cancelled',
      );
      return ResponseEntity.success(true);
    } catch (e) {
      return ResponseEntity.failure(
        DisplayableError('Failed to cancel booking'),
      );
    }
  }
}
