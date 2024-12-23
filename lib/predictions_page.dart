import 'package:flutter/material.dart';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:intl/intl.dart';
import 'package:fl_chart/fl_chart.dart';
import 'dart:async';

class PredictionsPage extends StatelessWidget {
  const PredictionsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const PredictionsContent();
  }
}

class PredictionsContent extends StatefulWidget {
  const PredictionsContent({super.key});

  @override
  State<PredictionsContent> createState() => _PredictionsContentState();
}

class _PredictionsContentState extends State<PredictionsContent> {
  static const cacheDuration = Duration(minutes: 15);
  DateTime? _lastFetchTime;
  List<QueryDocumentSnapshot>? _cachedPredictions;
  StreamSubscription<QuerySnapshot>? _subscription;
  final _streamController =
      StreamController<List<QueryDocumentSnapshot>>.broadcast();
  Timer? _cacheTimer;
  bool _isStreamActive = false;

  @override
  void initState() {
    super.initState();
    _setupPredictionsStream();
  }

  @override
  void dispose() {
    _subscription?.cancel();
    _streamController.close();
    _cacheTimer?.cancel();
    super.dispose();
  }

  void _setupPredictionsStream() {
    if (_isStreamActive) return;

    final query = FirebaseFirestore.instance
        .collection('freespace_data')
        .doc('Hallenbad_City')
        .collection('predictions');

    _subscription = query.snapshots().listen((snapshot) {
      _lastFetchTime = DateTime.now();
      _cachedPredictions = snapshot.docs;
      _streamController.add(snapshot.docs);

      // Start cache timer
      _cacheTimer?.cancel();
      _cacheTimer = Timer(cacheDuration, () {
        _isStreamActive = false;
        _subscription?.cancel();
      });
    });

    _isStreamActive = true;
  }

  void _refreshData() {
    _lastFetchTime = null;
    _cachedPredictions = null;
    _cacheTimer?.cancel();
    _subscription?.cancel();
    _isStreamActive = false;
    _setupPredictionsStream();
  }

  final periods = [
    'early_morning',
    'late_morning',
    'lunch',
    'afternoon',
    'after_work',
    'evening'
  ];

  String getPeriodDisplayName(String period) {
    final Map<String, String> periodNames = {
      'early_morning': '6-9',
      'late_morning': '9-11',
      'lunch': '11-13',
      'afternoon': '13-16',
      'after_work': '16-19',
      'evening': '19-22',
    };
    return periodNames[period] ?? period;
  }

  DateTime _parseTimestamp(dynamic timestamp) {
    if (timestamp is Timestamp) {
      return timestamp.toDate();
    } else if (timestamp is String) {
      return DateTime.parse(timestamp);
    }
    throw FormatException('Invalid timestamp format: $timestamp');
  }

  @override
  Widget build(BuildContext context) {
    final screenWidth = MediaQuery.of(context).size.width;
    final isLargeScreen = screenWidth > 600;
    final contentWidth = isLargeScreen ? 600.0 : screenWidth;

    // Check if we need to reactivate the stream
    if (!_isStreamActive &&
        (_lastFetchTime == null ||
            DateTime.now().difference(_lastFetchTime!) >= cacheDuration)) {
      _setupPredictionsStream();
    }

    return Container(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF1E3C72), Color(0xFF2A5298)],
        ),
      ),
      child: Scaffold(
        backgroundColor: Colors.transparent,
        extendBodyBehindAppBar: true,
        extendBody: true,
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          elevation: 0,
          title: const Text('Occupancy Predictions'),
          actions: [
            Padding(
              padding: const EdgeInsets.only(right: 16.0),
              child: IconButton(
                icon: const Icon(Icons.refresh),
                onPressed: _refreshData,
              ),
            ),
          ],
        ),
        body: SafeArea(
          child: StreamBuilder<List<QueryDocumentSnapshot>>(
            stream: _streamController.stream,
            initialData: _cachedPredictions,
            builder: (context, snapshot) {
              if (snapshot.hasError) {
                return const Center(child: Text('Something went wrong'));
              }

              if (!snapshot.hasData && _lastFetchTime == null) {
                return const Center(child: CircularProgressIndicator());
              }

              // Use cached data if within cache duration
              final predictions = (_lastFetchTime != null &&
                      DateTime.now().difference(_lastFetchTime!) <
                          cacheDuration)
                  ? _cachedPredictions
                  : snapshot.data;

              final today = DateTime.now();
              final formattedToday = DateFormat('yyyy-MM-dd').format(today);

              // Handle null safety for predictions list
              final validPredictions = (predictions ?? [])
                  .where((doc) => doc.id.compareTo(formattedToday) >= 0)
                  .take(6)
                  .toList();

              // Sort the predictions
              validPredictions.sort((a, b) => a.id.compareTo(b.id));

              if (validPredictions.isEmpty) {
                return const Center(child: Text('No predictions available'));
              }

              return ListView.builder(
                padding: const EdgeInsets.all(16),
                itemCount: validPredictions.length,
                itemBuilder: (context, index) {
                  final doc = validPredictions[index];
                  final data = doc.data() as Map<String, dynamic>;
                  final date = DateTime.parse(doc.id);
                  final dayName = DateFormat('EEEE').format(date);

                  return Card(
                    color: Colors.white.withOpacity(0.1),
                    margin: const EdgeInsets.only(bottom: 16),
                    child: ExpansionTile(
                      title: Text(
                        dayName,
                        style: const TextStyle(color: Colors.white),
                      ),
                      subtitle: Text(
                        DateFormat('yyyy-MM-dd').format(date),
                        style: TextStyle(color: Colors.white.withOpacity(0.7)),
                      ),
                      children: [
                        Padding(
                          padding: const EdgeInsets.all(16),
                          child: Column(
                            children: periods.map((period) {
                              final periodsData = data['periods'] as Map<String, dynamic>?;
                              final periodData = periodsData?[period] as Map<String, dynamic>?;
                              
                              // Skip period if no data or already passed
                              if (periodData == null || 
                                  (doc.id == DateFormat('yyyy-MM-dd').format(DateTime.now()) && 
                                   _isPastPeriod(period, DateTime.now()))) {
                                return const SizedBox.shrink();
                              }

                              final percentage = periodData['predicted_freespace_percentage'] ?? 0.0;
                              final predictions = data['predictions'] ?? [];
                              
                              // Check if there are any predictions for this period
                              final hasPredictions = predictions is List && 
                                predictions.any((p) {
                                  try {
                                    final timestamp = _parseTimestamp(p['timestamp']);
                                    final hour = timestamp.hour;
                                    final periodHours = {
                                      'early_morning': [6, 7, 8],
                                      'late_morning': [9, 10],
                                      'lunch': [11, 12],
                                      'afternoon': [13, 14, 15],
                                      'after_work': [16, 17, 18],
                                      'evening': [19, 20, 21],
                                    };
                                    return periodHours[period]?.contains(hour) ?? false;
                                  } catch (e) {
                                    return false;
                                  }
                                });

                              // Only show period if it has predictions
                              if (!hasPredictions) {
                                return const SizedBox.shrink();
                              }

                              return _buildPeriodRow(period, percentage, predictions);
                            }).toList()
                            ..removeWhere((widget) => widget is SizedBox && widget.width == null),
                          ),
                        ),
                      ],
                    ),
                  );
                },
              );
            },
          ),
        ),
      ),
    );
  }

  Widget _buildPeriodRow(String period, double percentage, dynamic predictions) {
    // Safely handle null predictions
    if (predictions == null) {
      return const SizedBox.shrink();
    }

    final now = DateTime.now();
    final today = DateFormat('yyyy-MM-dd').format(now);
    
    // Safely access first prediction timestamp
    final rowDate = predictions is List && predictions.isNotEmpty && predictions[0] != null
        ? DateFormat('yyyy-MM-dd').format(_parseTimestamp(predictions[0]['timestamp']))
        : '';

    // Only filter past periods if this row is for today
    if (rowDate == today && _isPastPeriod(period, now)) {
      return const SizedBox.shrink();
    }

    // Safely cast and filter predictions
    final predictionsList = ((predictions is List)
        ? predictions.where((p) => p != null).map((p) {
            try {
              final pred = p as Map<String, dynamic>;
              if (pred['timestamp'] == null) return null;
              final timestamp = _parseTimestamp(pred['timestamp']);
              if (pred['predicted_freespace_percentage'] == null ||
                  pred['lower_bound'] == null ||
                  pred['upper_bound'] == null) {
                return null;
              }
              return pred;
            } catch (e) {
              print('Error processing prediction: $e');
              return null;
            }
          }).whereType<Map<String, dynamic>>()
        : <Map<String, dynamic>>[]).where((prediction) {
      try {
        final timestamp = _parseTimestamp(prediction['timestamp']);
        final hour = timestamp.hour;

        final Map<String, List<int>> periodHours = {
          'early_morning': [6, 7, 8],
          'late_morning': [9, 10],
          'lunch': [11, 12],
          'afternoon': [13, 14, 15],
          'after_work': [16, 17, 18],
          'evening': [19, 20, 21],
        };

        return periodHours[period]?.contains(hour) ?? false;
      } catch (e) {
        print('Error filtering prediction: $e');
        return false;
      }
    }).toList()
      ..sort((a, b) {
        try {
          final aTime = _parseTimestamp(a['timestamp']);
          final bTime = _parseTimestamp(b['timestamp']);
          return aTime.compareTo(bTime);
        } catch (e) {
          print('Error sorting predictions: $e');
          return 0;
        }
      });

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: GestureDetector(
        onTap: () {
          showDialog(
            context: context,
            builder: (context) {
              return Dialog(
                child: SingleChildScrollView(
                  child: Container(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Detailed Predictions for ${getPeriodDisplayName(period)}',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const SizedBox(height: 16),
                        if (predictionsList.isEmpty)
                          const Text('No detailed predictions available')
                        else
                          ...predictionsList.map((data) {
                            final timestamp =
                                _parseTimestamp(data['timestamp']);
                            // Normalize minutes to 00 or 30
                            final normalizedMinutes =
                                timestamp.minute < 30 ? "00" : "30";
                            final timeStr =
                                "${timestamp.hour}:$normalizedMinutes";
                            final predicted =
                                data['predicted_freespace_percentage']
                                    as double;
                            final lower = data['lower_bound'] as double;
                            final upper = data['upper_bound'] as double;

                            return Padding(
                              padding: const EdgeInsets.symmetric(vertical: 8),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    timeStr,
                                    style:
                                        Theme.of(context).textTheme.titleMedium,
                                  ),
                                  const SizedBox(height: 4),
                                  Row(
                                    children: [
                                      Expanded(
                                        child: LinearProgressIndicator(
                                          value: predicted / 100,
                                          backgroundColor: Colors.grey[200],
                                          valueColor:
                                              AlwaysStoppedAnimation<Color>(
                                            _getColorForPercentage(predicted),
                                          ),
                                          minHeight: 20,
                                        ),
                                      ),
                                      const SizedBox(width: 8),
                                      Text('${predicted.round()}%'),
                                    ],
                                  ),
                                  Text(
                                    'Range: ${lower.round()}% - ${upper.round()}%',
                                    style:
                                        Theme.of(context).textTheme.bodySmall,
                                  ),
                                ],
                              ),
                            );
                          }).toList(),
                        const SizedBox(height: 16),
                        Align(
                          alignment: Alignment.centerRight,
                          child: TextButton(
                            onPressed: () => Navigator.pop(context),
                            child: const Text('Close'),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              );
            },
          );
        },
        child: Row(
          children: [
            SizedBox(
              width: 80,
              child: Text(
                getPeriodDisplayName(period),
                style: const TextStyle(color: Colors.white),
              ),
            ),
            Expanded(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: percentage / 100,
                  backgroundColor: Colors.white.withOpacity(0.1),
                  valueColor: AlwaysStoppedAnimation<Color>(
                      _getColorForPercentage(percentage)),
                  minHeight: 20,
                ),
              ),
            ),
            SizedBox(
              width: 50,
              child: Text(
                '${percentage.round()}%',
                textAlign: TextAlign.end,
                style: const TextStyle(color: Colors.white),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Color _getColorForPercentage(double percentage) {
    if (percentage >= 75) return Colors.green;
    if (percentage >= 50) return Colors.yellow;
    return Colors.red;
  }

  // Add helper method to check if a period is in the past
  bool _isPastPeriod(String period, DateTime now) {
    final periodEndTimes = {
      'early_morning': 9,
      'late_morning': 11,
      'lunch': 13,
      'afternoon': 16,
      'after_work': 19,
      'evening': 22,
    };

    final endHour = periodEndTimes[period] ?? 0;
    return now.hour >= endHour;
  }
}
