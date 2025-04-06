# Badi Counts

Real-time capacity monitoring and prediction system for Zurich's Hallenbad City swimming pool. The app helps visitors plan their swim sessions by providing current occupancy data and AI-powered predictions of pool capacity.

## Features

- **Live Capacity Monitoring**: Real-time updates of current pool occupancy
- **Smart Predictions**: Machine learning-based forecasts for the next 5 days
- **Time Period Analysis**: Capacity predictions broken down into six daily periods:
  - Early Morning (6:00-9:00)
  - Late Morning (9:00-11:00)
  - Lunch (11:00-13:00)
  - Afternoon (13:00-16:00)
  - After Work (16:00-19:00)
  - Evening (19:00-22:00)
- **Interactive Visualization**: Color-coded occupancy levels and detailed hourly charts
- **Offline Support**: Local caching of predictions for faster access

## Technology Stack

### Frontend
- Flutter framework for cross-platform deployment
- Material Design 3 with custom theming
- FL Chart for data visualization
- Firebase Analytics for usage tracking

### Backend
- Firebase Cloud Functions
- Cloud Firestore for data storage
- Python for data processing and ML predictions
- WebSocket integration with Badi monitoring system

## Setup

### Prerequisites
- Flutter SDK
- Python 3.8+
- Firebase CLI
- Google Cloud account with Firebase project

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/badi_counts.git
   cd badi_counts
   ```

2. **Frontend Setup**
   ```bash
   flutter pub get
   ```

3. **Backend Setup**
   ```bash
   cd functions
   pip install -r requirements.txt
   ```

4. **Firebase Configuration**
   - Create a new Firebase project
   - Enable Firestore and Cloud Functions
   - Add your Firebase configuration to `lib/firebase_options.dart`
   - Configure environment variables:
     ```bash
     # In Firebase Functions environment
     DBOS_PREDICT_URL=your_prediction_service_url
     ```

## Development

### Running Locally

1. **Start the Flutter app**
   ```bash
   flutter run
   ```

2. **Run Cloud Functions locally**
   ```bash
   cd functions
   firebase emulators:start
   ```

### Project Structure

```
badi_counts/
├── lib/                # Flutter app source
│   ├── main.dart      # App initialization and theme setup
│   ├── home_page.dart # Main dashboard view
│   └── widgets/       # Reusable UI components
├── functions/         # Python backend functions
│   ├── main.py       # Core functionality and scheduling
│   └── requirements.txt
├── dbos_fct/         # ML prediction service
├── public/           # Web assets
└── android/          # Android-specific configs
```

### Key Functions

- **Data Collection**: Automated collection of pool capacity data every 10 minutes (6:00-22:00)
- **Predictions**: ML model updates every 2 hours for accurate capacity forecasting
- **Caching**: Local storage of predictions with 30-minute validity
- **Period Analysis**: Smart aggregation of data into meaningful time periods

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - See the [LICENSE](LICENSE) file for details.
