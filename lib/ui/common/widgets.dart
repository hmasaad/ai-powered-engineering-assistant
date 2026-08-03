import 'package:flutter/material.dart';
import 'package:salon_booking/res/colors.dart';
import 'package:salon_booking/res/strings.dart';

class FullScreenLoader extends StatelessWidget {
  const FullScreenLoader({super.key});

  @override
  Widget build(BuildContext context) {
    return const Center(
      child: CircularProgressIndicator(color: AppColors.accent),
    );
  }
}

class FullScreenError extends StatelessWidget {
  final String message;
  final VoidCallback onRetryTap;

  const FullScreenError({
    required this.message,
    required this.onRetryTap,
    super.key,
  });

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              message,
              textAlign: TextAlign.center,
              style: const TextStyle(color: AppColors.muted, fontSize: 16),
            ),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: onRetryTap,
              style: FilledButton.styleFrom(backgroundColor: AppColors.accent),
              child: const Text(Strings.retry),
            ),
          ],
        ),
      ),
    );
  }
}
