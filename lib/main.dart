import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:salon_booking/inject/injector.dart';
import 'package:salon_booking/res/colors.dart';
import 'package:salon_booking/res/strings.dart';
import 'package:salon_booking/ui/tabbar/tab_bar_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Injector.setup();
  runApp(const SalonBookingApp());
}

class SalonBookingApp extends StatelessWidget {
  const SalonBookingApp({super.key});

  @override
  Widget build(BuildContext context) {
    final base = ThemeData(
      colorScheme: ColorScheme.fromSeed(
        seedColor: AppColors.accent,
        brightness: Brightness.light,
      ),
      scaffoldBackgroundColor: AppColors.surface,
      useMaterial3: true,
    );

    return MaterialApp(
      title: Strings.appName,
      debugShowCheckedModeBanner: false,
      theme: base.copyWith(
        textTheme: GoogleFonts.dmSansTextTheme(base.textTheme),
      ),
      home: const TabBarScreen(),
    );
  }
}
