import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:intl/intl.dart';
import 'package:salon_booking/api/entities/salon.dart';
import 'package:salon_booking/blocs/booking_bloc.dart';
import 'package:salon_booking/core/contracts/booking_contract.dart';
import 'package:salon_booking/core/screen_state.dart';
import 'package:salon_booking/core/view_actions.dart';
import 'package:salon_booking/inject/injector.dart';
import 'package:salon_booking/res/colors.dart';
import 'package:salon_booking/res/strings.dart';
import 'package:salon_booking/ui/common/widgets.dart';

class BookingScreen extends StatefulWidget {
  final Salon salon;
  final SalonServiceItem service;

  const BookingScreen({
    required this.salon,
    required this.service,
    super.key,
  });

  @override
  State<BookingScreen> createState() => _BookingScreenState();
}

class _BookingScreenState extends State<BookingScreen> {
  late final BookingBloc bloc;

  @override
  void initState() {
    super.initState();
    bloc = Injector.createBookingBloc(
      salon: widget.salon,
      service: widget.service,
    );
    bloc.viewActions.listen(_onViewAction);
    bloc.add(InitBookingEvent());
  }

  void _onViewAction(ViewAction event) {
    if (event is NavigateScreen &&
        event.target == BookingTarget.bookingConfirmed) {
      final booking = event.data as Booking;
      showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Booking confirmed'),
          content: Text(
            '${booking.serviceName} with ${booking.stylistName}\n'
            '${DateFormat.yMMMd().add_jm().format(booking.start)}',
          ),
          actions: [
            TextButton(
              onPressed: () {
                Navigator.of(context).pop();
                Navigator.of(context).popUntil((route) => route.isFirst);
              },
              child: const Text('Done'),
            ),
          ],
        ),
      );
    } else if (event is DisplayMessage) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(event.message)),
      );
    }
  }

  @override
  void dispose() {
    bloc.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final dayFormat = DateFormat.MMMd();
    final timeFormat = DateFormat.jm();

    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(
        backgroundColor: AppColors.surface,
        elevation: 0,
        foregroundColor: AppColors.ink,
        title: Text(widget.service.name),
      ),
      body: BlocProvider(
        create: (_) => bloc,
        child: BlocBuilder<BookingBloc, BookingData>(
          builder: (context, data) {
            if (data.state == ScreenState.error) {
              return FullScreenError(
                message: data.errorMessage ?? Strings.noInternet,
                onRetryTap: () => bloc.add(InitBookingEvent()),
              );
            }

            return Column(
              children: [
                Expanded(
                  child: data.state == ScreenState.loading &&
                          data.stylists.isEmpty
                      ? const FullScreenLoader()
                      : ListView(
                          padding: const EdgeInsets.all(20),
                          children: [
                            Text(
                              widget.salon.name,
                              style: const TextStyle(
                                fontWeight: FontWeight.w700,
                                fontSize: 18,
                              ),
                            ),
                            Text(
                              '\$${widget.service.price.toStringAsFixed(0)} · ${widget.service.durationMinutes} min',
                              style: const TextStyle(color: AppColors.muted),
                            ),
                            const SizedBox(height: 20),
                            const Text(
                              Strings.stylists,
                              style: TextStyle(
                                fontWeight: FontWeight.w700,
                                fontSize: 16,
                              ),
                            ),
                            const SizedBox(height: 10),
                            Wrap(
                              spacing: 8,
                              runSpacing: 8,
                              children: data.stylists.map((stylist) {
                                final selected =
                                    data.selectedStylist?.id == stylist.id;
                                return ChoiceChip(
                                  label: Text(stylist.name),
                                  selected: selected,
                                  onSelected: (_) =>
                                      bloc.add(SelectStylistEvent(stylist)),
                                  selectedColor: AppColors.accentSoft,
                                );
                              }).toList(),
                            ),
                            const SizedBox(height: 20),
                            Row(
                              children: [
                                const Text(
                                  'Day',
                                  style: TextStyle(
                                    fontWeight: FontWeight.w700,
                                    fontSize: 16,
                                  ),
                                ),
                                const Spacer(),
                                TextButton(
                                  onPressed: () async {
                                    final picked = await showDatePicker(
                                      context: context,
                                      initialDate: data.selectedDay,
                                      firstDate: DateTime.now(),
                                      lastDate: DateTime.now()
                                          .add(const Duration(days: 60)),
                                    );
                                    if (picked != null) {
                                      bloc.add(SelectDayEvent(picked));
                                    }
                                  },
                                  child: Text(dayFormat.format(data.selectedDay)),
                                ),
                              ],
                            ),
                            const SizedBox(height: 8),
                            const Text(
                              Strings.pickTime,
                              style: TextStyle(
                                fontWeight: FontWeight.w700,
                                fontSize: 16,
                              ),
                            ),
                            const SizedBox(height: 10),
                            if (data.state == ScreenState.loading)
                              const Padding(
                                padding: EdgeInsets.all(24),
                                child: Center(child: CircularProgressIndicator()),
                              )
                            else
                              Wrap(
                                spacing: 8,
                                runSpacing: 8,
                                children: data.timeSlots.map((slot) {
                                  final selected =
                                      data.selectedSlot?.id == slot.id;
                                  return ChoiceChip(
                                    label: Text(timeFormat.format(slot.start)),
                                    selected: selected,
                                    onSelected: slot.isAvailable
                                        ? (_) =>
                                            bloc.add(SelectTimeSlotEvent(slot))
                                        : null,
                                    selectedColor: AppColors.accentSoft,
                                  );
                                }).toList(),
                              ),
                            if (data.submitError != null) ...[
                              const SizedBox(height: 16),
                              Text(
                                data.submitError!,
                                style: const TextStyle(color: AppColors.danger),
                              ),
                            ],
                          ],
                        ),
                ),
                SafeArea(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(20, 8, 20, 16),
                    child: SizedBox(
                      width: double.infinity,
                      child: FilledButton(
                        onPressed: data.submitState == ScreenState.loading
                            ? null
                            : () => bloc.add(ConfirmBookingEvent()),
                        style: FilledButton.styleFrom(
                          backgroundColor: AppColors.accent,
                          padding: const EdgeInsets.symmetric(vertical: 14),
                        ),
                        child: data.submitState == ScreenState.loading
                            ? const SizedBox(
                                width: 20,
                                height: 20,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: Colors.white,
                                ),
                              )
                            : const Text(Strings.confirmBooking),
                      ),
                    ),
                  ),
                ),
              ],
            );
          },
        ),
      ),
    );
  }
}
