import 'package:flutter/material.dart';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_core/firebase_core.dart';
import 'firebase_options.dart'; // Import the generated Firebase options
import 'package:intl/intl.dart';
import 'package:fl_chart/fl_chart.dart'; // Add this import
import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';
import 'home_page.dart';
import 'package:firebase_analytics/firebase_analytics.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  await Firebase.initializeApp(
    options: DefaultFirebaseOptions.currentPlatform,
  );

  FirebaseAnalytics analytics = FirebaseAnalytics.instance;

  runApp(MyApp(analytics: analytics));
}

class MyApp extends StatelessWidget {
  final FirebaseAnalytics analytics;

  const MyApp({super.key, required this.analytics});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Hallenbad City - Live Capacity',
      theme: ThemeData(
        useMaterial3: true,
        fontFamily: 'Noto Sans', // Add this line
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF1E3C72),
          brightness: Brightness.dark,
        ),
        textTheme: const TextTheme(
          displayLarge: TextStyle(
            fontSize: 72,
            fontWeight: FontWeight.bold,
            color: Colors.white,
            fontFamily: 'Noto Sans', // Add this line
          ),
          headlineMedium: TextStyle(
            fontSize: 24,
            fontWeight: FontWeight.bold,
            color: Colors.white,
            fontFamily: 'Noto Sans', // Add this line
          ),
          titleLarge: TextStyle(
            fontSize: 20,
            fontWeight: FontWeight.bold,
            color: Colors.white,
            fontFamily: 'Noto Sans', // Add this line
          ),
        ),
      ),
      darkTheme: ThemeData.dark().copyWith(
        scaffoldBackgroundColor: const Color(0xFF1E3C72),
      ),
      home: const HomePage(),
      navigatorObservers: [
        FirebaseAnalyticsObserver(analytics: analytics),
      ],
    );
  }
}

void logging(String message) {
  print(message);
}

class PredictionsPage extends StatefulWidget {
  const PredictionsPage({super.key});

  @override
  State<PredictionsPage> createState() => _PredictionsPageState();
}

class _PredictionsPageState extends State<PredictionsPage> {
  late Future<Map<String, dynamic>> _predictionsFuture;

  @override
  void initState() {
    super.initState();
    _predictionsFuture = _fetchPredictions();
  }

  Future<void> _refreshPredictions() async {
    final prefs = await SharedPreferences.getInstance();
    // Clear the cache
    await prefs.remove('predictions_cache');
    await prefs.remove('predictions_cache_timestamp');

    setState(() {
      _predictionsFuture = _fetchPredictions();
    });
  }

  Future<Map<String, dynamic>> _fetchPredictions(
      {bool forceRefresh = false}) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final now = DateTime.now();
      final cacheKey = 'predictions_cache';
      final cacheTimestampKey = 'predictions_cache_timestamp';

      // Check cache only if not forcing refresh
      if (!forceRefresh) {
        final cachedTimestamp = prefs.getInt(cacheTimestampKey);
        if (cachedTimestamp != null) {
          final cacheAge = now
              .difference(DateTime.fromMillisecondsSinceEpoch(cachedTimestamp));
          if (cacheAge.inMinutes < 30) {
            final cachedData = prefs.getString(cacheKey);
            if (cachedData != null) {
              logging('Using cached predictions data');
              return _restoreTimestamps(json.decode(cachedData));
            }
          }
        }
      }

      // If no cache or cache is old, fetch from Firestore
      final today = DateTime.now();
      final todayString = DateFormat('yyyy-MM-dd').format(today);

      final predictionsRef = FirebaseFirestore.instance
          .collection('freespace_data')
          .doc('Hallenbad_City')
          .collection('predictions')
          .where(FieldPath.documentId, isGreaterThanOrEqualTo: todayString);

      final querySnapshot = await predictionsRef.get();
      final predictions = <String, dynamic>{};

      for (var doc in querySnapshot.docs) {
        predictions[doc.id] = doc.data();
      }

      if (predictions.isEmpty) {
        logging('No predictions found for today onwards.');
      } else {
        // Convert Timestamps to ISO strings before caching
        final serializableData = _makeSerializable(predictions);
        await prefs.setString(cacheKey, json.encode(serializableData));
        await prefs.setInt(cacheTimestampKey, now.millisecondsSinceEpoch);
        logging('Cached new predictions data');
        logging('Fetched predictions: ${predictions.keys.toList()}');
      }
      return predictions;
    } catch (e) {
      logging('Error fetching predictions: $e');
      return {};
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Predictions'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _refreshPredictions,
            tooltip: 'Force refresh predictions',
          ),
        ],
      ),
      body: FutureBuilder<Map<String, dynamic>>(
        future: _predictionsFuture,
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          } else if (snapshot.hasError) {
            return Center(child: Text('Error: ${snapshot.error}'));
          } else if (!snapshot.hasData || snapshot.data!.isEmpty) {
            return const Center(child: Text('No predictions available'));
          } else {
            return PredictionsTable(predictions: snapshot.data!);
          }
        },
      ),
    );
  }

  // Helper methods (_makeSerializable and _restoreTimestamps) remain the same
  Map<String, dynamic> _makeSerializable(Map<String, dynamic> data) {
    return data.map((key, value) {
      if (value is Map) {
        return MapEntry(key, _makeSerializable(value.cast<String, dynamic>()));
      } else if (value is List) {
        return MapEntry(
            key,
            value.map((item) {
              if (item is Map) {
                var newItem = Map<String, dynamic>.from(item);
                if (newItem['timestamp'] is Timestamp) {
                  newItem['timestamp'] = (newItem['timestamp'] as Timestamp)
                      .toDate()
                      .toIso8601String();
                }
                return newItem;
              }
              return item;
            }).toList());
      } else if (value is Timestamp) {
        return MapEntry(key, value.toDate().toIso8601String());
      }
      return MapEntry(key, value);
    });
  }

  // Helper method to restore Timestamps from cached data
  Map<String, dynamic> _restoreTimestamps(Map<String, dynamic> data) {
    return data.map((key, value) {
      if (value is Map) {
        return MapEntry(key, _restoreTimestamps(value.cast<String, dynamic>()));
      } else if (value is List) {
        return MapEntry(
            key,
            value.map((item) {
              if (item is Map) {
                var newItem = Map<String, dynamic>.from(item);
                if (newItem['timestamp'] is String) {
                  newItem['timestamp'] =
                      Timestamp.fromDate(DateTime.parse(newItem['timestamp']));
                }
                return newItem;
              }
              return item;
            }).toList());
      } else if (key == 'timestamp' && value is String) {
        return MapEntry(key, Timestamp.fromDate(DateTime.parse(value)));
      }
      return MapEntry(key, value);
    });
  }
}

class PredictionsTable extends StatelessWidget {
  final Map<String, dynamic> predictions;

  PredictionsTable({super.key, required this.predictions});

  final List<String> periods = [
    'early_morning',
    'late_morning',
    'lunch',
    'afternoon',
    'after_work',
    'evening'
  ];

  final Map<String, Map<String, int>> periodTimes = {
    'early_morning': {'start': 6, 'end': 9},
    'late_morning': {'start': 9, 'end': 11},
    'lunch': {'start': 11, 'end': 13},
    'afternoon': {'start': 13, 'end': 16},
    'after_work': {'start': 16, 'end': 19},
    'evening': {'start': 19, 'end': 22},
  };

  // Helper method to get display name for period
  String getPeriodDisplayName(String period) {
    switch (period) {
      case 'early_morning':
        return '6-9';
      case 'late_morning':
        return '9-11';
      case 'lunch':
        return '11-13';
      case 'afternoon':
        return '13-16';
      case 'after_work':
        return '16-19';
      case 'evening':
        return '19-22';
      default:
        return period;
    }
  }

  List<String> _generateDays() {
    final today = DateTime.now();
    final days = List.generate(6, (index) {
      final date = today.add(Duration(days: index));
      return DateFormat('yyyy-MM-dd').format(date); // Return date string
    });
    return days;
  }

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final days = _generateDays();

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: DataTable(
        columns: [
          const DataColumn(label: Text('Day')),
          ...periods.map((period) =>
              DataColumn(label: Text(getPeriodDisplayName(period)))),
        ],
        rows: days.map((dateString) {
          final isToday = dateString == DateFormat('yyyy-MM-dd').format(now);
          final DateTime date = DateTime.parse(dateString);
          final dayName = DateFormat('EEEE').format(date);
          final dayKey = dateString;

          if (!predictions.containsKey(dayKey)) {
            logging('No predictions data for $dayName.');
          }

          return DataRow(
            cells: [
              DataCell(Text(dayName)),
              ...periods.map((period) {
                final prediction = predictions[dayKey]?['periods']?[period];
                final percentage = prediction != null
                    ? prediction['predicted_freespace_percentage']
                    : 'N/A';
                final color = _getColorForPercentage(percentage);
                final periodEnd = periodTimes[period]?['end'] ?? 24;
                final isPast = isToday && now.hour >= periodEnd;
                final cellColor = isPast ? Colors.grey[400] : color;
                final displayText = isPast ? '-' : '$percentage%';

                return DataCell(
                  GestureDetector(
                    onTap: (isPast || percentage == 'N/A')
                        ? null
                        : () {
                            // Extract hourly predictions for the selected day and period
                            final hourlyPredictions = (predictions[dayKey]
                                    ?['predictions'] as List<dynamic>?)
                                ?.where((p) => p['time_period'] == period)
                                .map((p) => {
                                      'timestamp': p['timestamp'].toDate(),
                                      'predicted_freespace_percentage':
                                          p['predicted_freespace_percentage'],
                                    })
                                .toList();

                            showDialog(
                              context: context,
                              builder: (context) {
                                return AlertDialog(
                                  title: Text('$dayName - $period Predictions'),
                                  content: hourlyPredictions != null &&
                                          hourlyPredictions.isNotEmpty
                                      ? SizedBox(
                                          width: double.maxFinite,
                                          height:
                                              200, // Adjust height as needed
                                          child: BarChart(
                                            BarChartData(
                                              alignment:
                                                  BarChartAlignment.spaceAround,
                                              maxY: 100,
                                              barTouchData:
                                                  BarTouchData(enabled: false),
                                              titlesData: FlTitlesData(
                                                leftTitles: AxisTitles(
                                                  sideTitles: SideTitles(
                                                      showTitles: true,
                                                      reservedSize: 28),
                                                ),
                                                bottomTitles: AxisTitles(
                                                  sideTitles: SideTitles(
                                                    showTitles: true,
                                                    getTitlesWidget:
                                                        (double value,
                                                            TitleMeta meta) {
                                                      final index =
                                                          value.toInt();
                                                      if (index >= 0 &&
                                                          index <
                                                              hourlyPredictions
                                                                  .length) {
                                                        return Text(DateFormat(
                                                                'HH:mm')
                                                            .format(
                                                                hourlyPredictions[
                                                                        index][
                                                                    'timestamp']));
                                                      }
                                                      return const Text('');
                                                    },
                                                  ),
                                                ),
                                              ),
                                              borderData:
                                                  FlBorderData(show: false),
                                              barGroups: hourlyPredictions
                                                  .asMap()
                                                  .entries
                                                  .map((entry) {
                                                int idx = entry.key;
                                                var prediction = entry.value;
                                                return BarChartGroupData(
                                                  x: idx,
                                                  barRods: [
                                                    BarChartRodData(
                                                      fromY: 0,
                                                      toY: prediction[
                                                              'predicted_freespace_percentage']
                                                          .toDouble(),
                                                      color: Colors.blue,
                                                      width: 16,
                                                      borderRadius:
                                                          BorderRadius.circular(
                                                              4),
                                                    ),
                                                  ],
                                                );
                                              }).toList(),
                                            ),
                                          ),
                                        )
                                      : const Text(
                                          'No hourly predictions available.'),
                                  actions: [
                                    TextButton(
                                      onPressed: () => Navigator.pop(context),
                                      child: const Text('Close'),
                                    ),
                                  ],
                                );
                              },
                            );
                          },
                    child: Container(
                      color: cellColor,
                      padding: const EdgeInsets.all(8.0),
                      child: Text(displayText),
                    ),
                  ),
                );
              }).toList(),
            ],
          );
        }).toList(),
      ),
    );
  }

  Color _getColorForPercentage(dynamic percentage) {
    if (percentage == 'N/A') return Colors.grey;
    final value = double.tryParse(percentage.toString()) ?? 0;
    if (value >= 75) return Colors.green;
    if (value >= 50) return Colors.yellow;
    return Colors.red;
  }
}
