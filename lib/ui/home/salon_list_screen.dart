import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:salon_booking/api/entities/salon.dart';
import 'package:salon_booking/blocs/salon_list_bloc.dart';
import 'package:salon_booking/core/base_state.dart';
import 'package:salon_booking/core/contracts/salon_list_contract.dart';
import 'package:salon_booking/core/screen_state.dart';
import 'package:salon_booking/core/view_actions.dart';
import 'package:salon_booking/res/colors.dart';
import 'package:salon_booking/res/strings.dart';
import 'package:salon_booking/ui/common/widgets.dart';
import 'package:salon_booking/ui/salon_details/salon_details_screen.dart';

class SalonListScreen extends StatefulWidget {
  const SalonListScreen({super.key});

  @override
  State<SalonListScreen> createState() => _SalonListScreenState();
}

class _SalonListScreenState
    extends BaseState<SalonListBloc, SalonListScreen> {
  final _searchController = TextEditingController();

  @override
  void initState() {
    bloc.add(InitSalonListEvent());
    super.initState();
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  void onNavigationEvent(NavigateScreen event) {
    if (event.target == SalonListTarget.salonDetails && event.data is String) {
      Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => SalonDetailsScreen(salonId: event.data as String),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.surface,
      body: SafeArea(
        child: BlocProvider(
          create: (_) => bloc,
          child: BlocBuilder<SalonListBloc, SalonListData>(
            builder: (context, data) {
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(20, 16, 20, 8),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          Strings.appName,
                          style: Theme.of(context).textTheme.headlineMedium
                              ?.copyWith(
                            fontWeight: FontWeight.w700,
                            color: AppColors.ink,
                          ),
                        ),
                        const SizedBox(height: 4),
                        const Text(
                          Strings.findSalon,
                          style: TextStyle(color: AppColors.muted),
                        ),
                        const SizedBox(height: 16),
                        TextField(
                          controller: _searchController,
                          onChanged: (value) =>
                              bloc.add(SearchSalonsEvent(value)),
                          decoration: InputDecoration(
                            hintText: Strings.searchHint,
                            filled: true,
                            fillColor: AppColors.card,
                            prefixIcon: const Icon(Icons.search),
                            border: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(14),
                              borderSide:
                                  const BorderSide(color: AppColors.border),
                            ),
                            enabledBorder: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(14),
                              borderSide:
                                  const BorderSide(color: AppColors.border),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  Expanded(child: _buildBody(data)),
                ],
              );
            },
          ),
        ),
      ),
    );
  }

  Widget _buildBody(SalonListData data) {
    switch (data.state) {
      case ScreenState.loading:
        return const FullScreenLoader();
      case ScreenState.error:
        return FullScreenError(
          message: data.errorMessage ?? Strings.noInternet,
          onRetryTap: () => bloc.add(RetrySalonListEvent()),
        );
      case ScreenState.content:
        if (data.salons.isEmpty) {
          return const Center(
            child: Text(
              Strings.noResults,
              style: TextStyle(color: AppColors.muted),
            ),
          );
        }
        return ListView.separated(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
          itemCount: data.salons.length,
          separatorBuilder: (_, __) => const SizedBox(height: 12),
          itemBuilder: (context, index) {
            final salon = data.salons[index];
            return _SalonCard(
              salon: salon,
              onTap: () => bloc.add(OpenSalonDetailsEvent(salon.id)),
            );
          },
        );
    }
  }
}

class _SalonCard extends StatelessWidget {
  final Salon salon;
  final VoidCallback onTap;

  const _SalonCard({required this.salon, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.card,
      borderRadius: BorderRadius.circular(18),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(18),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(14),
                child: Image.network(
                  salon.imageUrl,
                  width: 84,
                  height: 84,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => Container(
                    width: 84,
                    height: 84,
                    color: AppColors.accentSoft,
                    child: const Icon(Icons.storefront, color: AppColors.accent),
                  ),
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      salon.name,
                      style: const TextStyle(
                        fontWeight: FontWeight.w700,
                        fontSize: 16,
                        color: AppColors.ink,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '${salon.address}, ${salon.city}',
                      style: const TextStyle(
                        color: AppColors.muted,
                        fontSize: 13,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        const Icon(Icons.star, size: 16, color: AppColors.accent),
                        const SizedBox(width: 4),
                        Text(
                          '${salon.rating} (${salon.reviewCount})',
                          style: const TextStyle(
                            fontWeight: FontWeight.w600,
                            fontSize: 13,
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
