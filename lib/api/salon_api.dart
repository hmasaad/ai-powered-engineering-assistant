import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:salon_booking/api/entities/common.dart';
import 'package:salon_booking/api/entities/salon.dart';

/// Sample API backed by local JSON asset data.
class SalonApi {
  Map<String, dynamic>? _cache;

  Future<Map<String, dynamic>> _load() async {
    if (_cache != null) return _cache!;
    await Future<void>.delayed(const Duration(milliseconds: 450));
    final raw = await rootBundle.loadString('assets/data/salon_booking.json');
    _cache = jsonDecode(raw) as Map<String, dynamic>;
    return _cache!;
  }

  Future<ResponseEntity<List<Salon>>> getSalons({
    String? query,
    String? tag,
  }) async {
    try {
      final data = await _load();
      var salons = (data['salons'] as List<dynamic>)
          .map((e) => Salon.fromJson(e as Map<String, dynamic>))
          .toList();
      if (tag != null && tag.trim().isNotEmpty) {
        final selected = tag.toLowerCase();
        salons = salons
            .where(
              (s) => s.tags.any((t) => t.toLowerCase() == selected),
            )
            .toList();
      }
      if (query != null && query.trim().isNotEmpty) {
        final q = query.toLowerCase();
        salons = salons
            .where(
              (s) =>
                  s.name.toLowerCase().contains(q) ||
                  s.city.toLowerCase().contains(q) ||
                  s.tags.any((t) => t.toLowerCase().contains(q)),
            )
            .toList();
      }
      return ResponseEntity.success(salons);
    } catch (e) {
      return ResponseEntity.failure(DisplayableError('Failed to load salons'));
    }
  }

  Future<ResponseEntity<Salon>> getSalonById(String salonId) async {
    try {
      final data = await _load();
      final salons = (data['salons'] as List<dynamic>)
          .map((e) => Salon.fromJson(e as Map<String, dynamic>))
          .toList();
      final salon = salons.cast<Salon?>().firstWhere(
            (s) => s!.id == salonId,
            orElse: () => null,
          );
      if (salon == null) {
        return ResponseEntity.failure(DisplayableError('Salon not found'));
      }
      return ResponseEntity.success(salon);
    } catch (e) {
      return ResponseEntity.failure(DisplayableError('Failed to load salon'));
    }
  }

  Future<ResponseEntity<List<SalonServiceItem>>> getServices(
    String salonId,
  ) async {
    try {
      final data = await _load();
      final services = (data['services'] as List<dynamic>)
          .map((e) => SalonServiceItem.fromJson(e as Map<String, dynamic>))
          .where((s) => s.salonId == salonId)
          .toList();
      return ResponseEntity.success(services);
    } catch (e) {
      return ResponseEntity.failure(DisplayableError('Failed to load services'));
    }
  }

  Future<ResponseEntity<List<Stylist>>> getStylists(
    String salonId, {
    String? serviceId,
  }) async {
    try {
      final data = await _load();
      var stylists = (data['stylists'] as List<dynamic>)
          .map((e) => Stylist.fromJson(e as Map<String, dynamic>))
          .where((s) => s.salonId == salonId)
          .toList();
      if (serviceId != null) {
        stylists =
            stylists.where((s) => s.serviceIds.contains(serviceId)).toList();
      }
      return ResponseEntity.success(stylists);
    } catch (e) {
      return ResponseEntity.failure(DisplayableError('Failed to load stylists'));
    }
  }

  Future<ResponseEntity<List<TimeSlot>>> getTimeSlots({
    required String stylistId,
    required DateTime day,
  }) async {
    try {
      await Future<void>.delayed(const Duration(milliseconds: 300));
      final slots = <TimeSlot>[];
      final base = DateTime(day.year, day.month, day.day, 9);
      for (var i = 0; i < 8; i++) {
        final start = base.add(Duration(hours: i));
        slots.add(
          TimeSlot(
            id: '$stylistId-${start.toIso8601String()}',
            stylistId: stylistId,
            start: start,
            isAvailable: i != 2 && i != 5,
          ),
        );
      }
      return ResponseEntity.success(slots);
    } catch (e) {
      return ResponseEntity.failure(
        DisplayableError('Failed to load time slots'),
      );
    }
  }
}
