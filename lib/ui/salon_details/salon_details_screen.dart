import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:salon_booking/api/entities/salon.dart';
import 'package:salon_booking/blocs/salon_details_bloc.dart';
import 'package:salon_booking/core/base_state.dart';
import 'package:salon_booking/core/contracts/salon_details_contract.dart';
import 'package:salon_booking/core/screen_state.dart';
import 'package:salon_booking/core/view_actions.dart';
import 'package:salon_booking/res/colors.dart';
import 'package:salon_booking/res/strings.dart';
import 'package:salon_booking/ui/booking/booking_screen.dart';
import 'package:salon_booking/ui/common/widgets.dart';

class SalonDetailsScreen extends StatefulWidget {
  final String salonId;

  const SalonDetailsScreen({required this.salonId, super.key});

  @override
  State<SalonDetailsScreen> createState() => _SalonDetailsScreenState();
}

class _SalonDetailsScreenState
    extends BaseState<SalonDetailsBloc, SalonDetailsScreen> {
  @override
  void initState() {
    bloc.add(InitSalonDetailsEvent(widget.salonId));
    super.initState();
  }

  @override
  void onNavigationEvent(NavigateScreen event) {
    if (event.target == SalonDetailsTarget.booking && event.data is Map) {
      final map = event.data as Map;
      Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => BookingScreen(
            salon: map['salon'] as Salon,
            service: map['service'] as SalonServiceItem,
          ),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(
        backgroundColor: AppColors.surface,
        elevation: 0,
        foregroundColor: AppColors.ink,
        title: const Text('Salon'),
      ),
      body: BlocProvider(
        create: (_) => bloc,
        child: BlocBuilder<SalonDetailsBloc, SalonDetailsData>(
          builder: (context, data) {
            switch (data.state) {
              case ScreenState.loading:
                return const FullScreenLoader();
              case ScreenState.error:
                return FullScreenError(
                  message: data.errorMessage ?? Strings.noInternet,
                  onRetryTap: () =>
                      bloc.add(RetrySalonDetailsEvent(widget.salonId)),
                );
              case ScreenState.content:
                return _DetailsBody(bloc: bloc, data: data);
            }
          },
        ),
      ),
    );
  }
}

class _DetailsBody extends StatelessWidget {
  final SalonDetailsBloc bloc;
  final SalonDetailsData data;

  const _DetailsBody({required this.bloc, required this.data});

  @override
  Widget build(BuildContext context) {
    final salon = data.salon!;
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 0, 20, 32),
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(18),
          child: AspectRatio(
            aspectRatio: 16 / 9,
            child: Image.network(
              salon.imageUrl,
              fit: BoxFit.cover,
              errorBuilder: (_, __, ___) => Container(
                color: AppColors.accentSoft,
                child: const Icon(Icons.storefront, size: 48),
              ),
            ),
          ),
        ),
        const SizedBox(height: 16),
        Text(
          salon.name,
          style: const TextStyle(
            fontSize: 24,
            fontWeight: FontWeight.w700,
            color: AppColors.ink,
          ),
        ),
        const SizedBox(height: 6),
        Text(
          '${salon.address}, ${salon.city}',
          style: const TextStyle(color: AppColors.muted),
        ),
        const SizedBox(height: 8),
        Text(
          salon.description,
          style: const TextStyle(color: AppColors.ink, height: 1.4),
        ),
        const SizedBox(height: 24),
        const Text(
          Strings.services,
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 12),
        ...data.services.map(
          (service) => Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: _ServiceTile(
              service: service,
              onBook: () => bloc.add(BookServiceEvent(service)),
            ),
          ),
        ),
        const SizedBox(height: 12),
        const Text(
          Strings.stylists,
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 12),
        SizedBox(
          height: 110,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            itemCount: data.stylists.length,
            separatorBuilder: (_, __) => const SizedBox(width: 12),
            itemBuilder: (context, index) {
              final stylist = data.stylists[index];
              return SizedBox(
                width: 120,
                child: Column(
                  children: [
                    CircleAvatar(
                      radius: 28,
                      backgroundImage: NetworkImage(stylist.imageUrl),
                      onBackgroundImageError: (_, __) {},
                      backgroundColor: AppColors.accentSoft,
                    ),
                    const SizedBox(height: 8),
                    Text(
                      stylist.name,
                      textAlign: TextAlign.center,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    Text(
                      stylist.title,
                      textAlign: TextAlign.center,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: AppColors.muted,
                        fontSize: 12,
                      ),
                    ),
                  ],
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}

class _ServiceTile extends StatelessWidget {
  final SalonServiceItem service;
  final VoidCallback onBook;

  const _ServiceTile({required this.service, required this.onBook});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.card,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  service.name,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 4),
                Text(
                  '${service.durationMinutes} min · \$${service.price.toStringAsFixed(0)}',
                  style: const TextStyle(color: AppColors.muted, fontSize: 13),
                ),
              ],
            ),
          ),
          FilledButton(
            onPressed: onBook,
            style: FilledButton.styleFrom(backgroundColor: AppColors.accent),
            child: const Text(Strings.bookNow),
          ),
        ],
      ),
    );
  }
}
