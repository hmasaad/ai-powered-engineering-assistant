import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:intl/intl.dart';
import 'package:salon_booking/blocs/my_bookings_bloc.dart';
import 'package:salon_booking/core/base_state.dart';
import 'package:salon_booking/core/contracts/my_bookings_contract.dart';
import 'package:salon_booking/core/screen_state.dart';
import 'package:salon_booking/core/view_actions.dart';
import 'package:salon_booking/res/colors.dart';
import 'package:salon_booking/res/strings.dart';
import 'package:salon_booking/ui/common/widgets.dart';

class MyBookingsScreen extends StatefulWidget {
  const MyBookingsScreen({super.key});

  @override
  State<MyBookingsScreen> createState() => _MyBookingsScreenState();
}

class _MyBookingsScreenState
    extends BaseState<MyBookingsBloc, MyBookingsScreen> {
  @override
  void initState() {
    bloc.add(InitMyBookingsEvent());
    super.initState();
  }

  @override
  void onViewEvent(ViewAction event) {
    if (event is DisplayMessage) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(event.message)),
      );
      return;
    }
    super.onViewEvent(event);
  }

  @override
  Widget build(BuildContext context) {
    final format = DateFormat.yMMMd().add_jm();

    return Scaffold(
      backgroundColor: AppColors.surface,
      body: SafeArea(
        child: BlocProvider(
          create: (_) => bloc,
          child: BlocBuilder<MyBookingsBloc, MyBookingsData>(
            builder: (context, data) {
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Padding(
                    padding: EdgeInsets.fromLTRB(20, 16, 20, 8),
                    child: Text(
                      Strings.myBookings,
                      style: TextStyle(
                        fontSize: 28,
                        fontWeight: FontWeight.w700,
                        color: AppColors.ink,
                      ),
                    ),
                  ),
                  Expanded(
                    child: switch (data.state) {
                      ScreenState.loading => const FullScreenLoader(),
                      ScreenState.error => FullScreenError(
                          message: data.errorMessage ?? Strings.noInternet,
                          onRetryTap: () =>
                              bloc.add(RetryMyBookingsEvent()),
                        ),
                      ScreenState.content => data.bookings.isEmpty
                          ? const Center(
                              child: Text(
                                'No bookings yet',
                                style: TextStyle(color: AppColors.muted),
                              ),
                            )
                          : ListView.separated(
                              padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
                              itemCount: data.bookings.length,
                              separatorBuilder: (_, __) =>
                                  const SizedBox(height: 12),
                              itemBuilder: (context, index) {
                                final booking = data.bookings[index];
                                final cancelled =
                                    booking.status == 'cancelled';
                                return Container(
                                  padding: const EdgeInsets.all(16),
                                  decoration: BoxDecoration(
                                    color: AppColors.card,
                                    borderRadius: BorderRadius.circular(16),
                                    border: Border.all(color: AppColors.border),
                                  ),
                                  child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        booking.serviceName,
                                        style: const TextStyle(
                                          fontWeight: FontWeight.w700,
                                          fontSize: 16,
                                        ),
                                      ),
                                      const SizedBox(height: 4),
                                      Text(
                                        '${booking.salonName} · ${booking.stylistName}',
                                        style: const TextStyle(
                                          color: AppColors.muted,
                                        ),
                                      ),
                                      const SizedBox(height: 8),
                                      Text(format.format(booking.start)),
                                      const SizedBox(height: 8),
                                      Row(
                                        children: [
                                          Text(
                                            booking.status.toUpperCase(),
                                            style: TextStyle(
                                              color: cancelled
                                                  ? AppColors.danger
                                                  : AppColors.accent,
                                              fontWeight: FontWeight.w700,
                                              fontSize: 12,
                                            ),
                                          ),
                                          const Spacer(),
                                          if (!cancelled)
                                            TextButton(
                                              onPressed: () => bloc.add(
                                                CancelBookingEvent(booking.id),
                                              ),
                                              child: const Text('Cancel'),
                                            ),
                                        ],
                                      ),
                                    ],
                                  ),
                                );
                              },
                            ),
                    },
                  ),
                ],
              );
            },
          ),
        ),
      ),
    );
  }
}
