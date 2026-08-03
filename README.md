# SalonBook

Flutter salon booking app using the same **contract-driven BLoC** architecture as our other apps (BaseBloc, feature contracts, get_it DI, services over APIs, `ScreenState` + `ViewActions`).

## Features

- Browse salons (sample JSON API)
- Salon details with services and stylists
- Book a service (stylist + day + time slot)
- View / cancel bookings

## Architecture

```
UI (BaseState) → Bloc (BaseBloc) → Service → Api → ResponseEntity
                     ↓
               ViewActions (navigation / toasts)
```

Sample data: `assets/data/salon_booking.json`

## Run

```bash
flutter pub get
flutter run
```

## Local PR Reviewer (Ollama — no Cursor Cloud)

```bash
# once
ollama serve
ollama pull llama3.2

# review current branch vs origin/main
./scripts/pr-review.sh
```

Rules: `agents/pr-reviewer/RULES.md`  
Details: `agents/pr-reviewer/README.md`
