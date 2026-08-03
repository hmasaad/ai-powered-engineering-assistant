import 'package:flutter_test/flutter_test.dart';
import 'package:salon_booking/inject/injector.dart';
import 'package:salon_booking/main.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('SalonBook app boots to salons tab', (tester) async {
    await Injector.setup();
    await tester.pumpWidget(const SalonBookingApp());
    expect(find.text('SalonBook'), findsOneWidget);
    await tester.pump(const Duration(seconds: 1));
  });
}
