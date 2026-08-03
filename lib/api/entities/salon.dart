import 'package:equatable/equatable.dart';

class Salon extends Equatable {
  final String id;
  final String name;
  final String address;
  final String city;
  final double rating;
  final int reviewCount;
  final String imageUrl;
  final String description;
  final List<String> tags;

  const Salon({
    required this.id,
    required this.name,
    required this.address,
    required this.city,
    required this.rating,
    required this.reviewCount,
    required this.imageUrl,
    required this.description,
    required this.tags,
  });

  factory Salon.fromJson(Map<String, dynamic> json) {
    return Salon(
      id: json['id'] as String,
      name: json['name'] as String,
      address: json['address'] as String,
      city: json['city'] as String,
      rating: (json['rating'] as num).toDouble(),
      reviewCount: json['reviewCount'] as int,
      imageUrl: json['imageUrl'] as String,
      description: json['description'] as String,
      tags: (json['tags'] as List<dynamic>).cast<String>(),
    );
  }

  @override
  List<Object?> get props =>
      [id, name, address, city, rating, reviewCount, imageUrl, description, tags];
}

class SalonServiceItem extends Equatable {
  final String id;
  final String salonId;
  final String name;
  final String description;
  final int durationMinutes;
  final double price;

  const SalonServiceItem({
    required this.id,
    required this.salonId,
    required this.name,
    required this.description,
    required this.durationMinutes,
    required this.price,
  });

  factory SalonServiceItem.fromJson(Map<String, dynamic> json) {
    return SalonServiceItem(
      id: json['id'] as String,
      salonId: json['salonId'] as String,
      name: json['name'] as String,
      description: json['description'] as String,
      durationMinutes: json['durationMinutes'] as int,
      price: (json['price'] as num).toDouble(),
    );
  }

  @override
  List<Object?> get props =>
      [id, salonId, name, description, durationMinutes, price];
}

class Stylist extends Equatable {
  final String id;
  final String salonId;
  final String name;
  final String title;
  final double rating;
  final String imageUrl;
  final List<String> serviceIds;

  const Stylist({
    required this.id,
    required this.salonId,
    required this.name,
    required this.title,
    required this.rating,
    required this.imageUrl,
    required this.serviceIds,
  });

  factory Stylist.fromJson(Map<String, dynamic> json) {
    return Stylist(
      id: json['id'] as String,
      salonId: json['salonId'] as String,
      name: json['name'] as String,
      title: json['title'] as String,
      rating: (json['rating'] as num).toDouble(),
      imageUrl: json['imageUrl'] as String,
      serviceIds: (json['serviceIds'] as List<dynamic>).cast<String>(),
    );
  }

  @override
  List<Object?> get props =>
      [id, salonId, name, title, rating, imageUrl, serviceIds];
}

class TimeSlot extends Equatable {
  final String id;
  final String stylistId;
  final DateTime start;
  final bool isAvailable;

  const TimeSlot({
    required this.id,
    required this.stylistId,
    required this.start,
    required this.isAvailable,
  });

  factory TimeSlot.fromJson(Map<String, dynamic> json) {
    return TimeSlot(
      id: json['id'] as String,
      stylistId: json['stylistId'] as String,
      start: DateTime.parse(json['start'] as String),
      isAvailable: json['isAvailable'] as bool,
    );
  }

  @override
  List<Object?> get props => [id, stylistId, start, isAvailable];
}

class Booking extends Equatable {
  final String id;
  final String salonId;
  final String salonName;
  final String serviceId;
  final String serviceName;
  final String stylistId;
  final String stylistName;
  final DateTime start;
  final double price;
  final String status;

  const Booking({
    required this.id,
    required this.salonId,
    required this.salonName,
    required this.serviceId,
    required this.serviceName,
    required this.stylistId,
    required this.stylistName,
    required this.start,
    required this.price,
    required this.status,
  });

  factory Booking.fromJson(Map<String, dynamic> json) {
    return Booking(
      id: json['id'] as String,
      salonId: json['salonId'] as String,
      salonName: json['salonName'] as String,
      serviceId: json['serviceId'] as String,
      serviceName: json['serviceName'] as String,
      stylistId: json['stylistId'] as String,
      stylistName: json['stylistName'] as String,
      start: DateTime.parse(json['start'] as String),
      price: (json['price'] as num).toDouble(),
      status: json['status'] as String,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'salonId': salonId,
        'salonName': salonName,
        'serviceId': serviceId,
        'serviceName': serviceName,
        'stylistId': stylistId,
        'stylistName': stylistName,
        'start': start.toIso8601String(),
        'price': price,
        'status': status,
      };

  @override
  List<Object?> get props => [
        id,
        salonId,
        salonName,
        serviceId,
        serviceName,
        stylistId,
        stylistName,
        start,
        price,
        status,
      ];
}
