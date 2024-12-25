import 'package:flutter/material.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'dart:convert';
import 'dart:async'; // Add this import for Timer
import 'package:intl/intl.dart';
import 'predictions_page.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:firebase_remote_config/firebase_remote_config.dart';

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  WebSocketChannel? _channel;
  bool _isConnected = false;
  int _reconnectAttempts = 0;
  static const int maxReconnectAttempts = 5;
  static const String _hasSeenWishesKey = 'has_seen_christmas_wishes';
  final _remoteConfig = FirebaseRemoteConfig.instance;
  static const String _showPopupKey = 'show_christmas_popup';
  Timer? _configCheckTimer;

  // Pool data
  int _freePlaces = 0;
  int _totalCapacity = 0;
  int _currentUsage = 0;
  double _availabilityPercentage = 0; // renamed from _occupancyPercentage
  String _status = 'Updating...';
  bool _isPoolOpen = false;
  String _timeRemaining = '';

  @override
  void initState() {
    super.initState();
    _initializeRemoteConfig();
    _connectWebSocket();
    _updateTimeRemaining();
    // Update time every second
    Timer.periodic(const Duration(seconds: 1), (_) => _updateTimeRemaining());

    // Add periodic config check every 5 minutes
    _configCheckTimer = Timer.periodic(const Duration(minutes: 5), (_) {
      _checkAndUpdateConfig();
    });
  }

  Future<void> _initializeRemoteConfig() async {
    try {
      debugPrint('Initializing Remote Config...');
      await _remoteConfig.setConfigSettings(RemoteConfigSettings(
        fetchTimeout: const Duration(minutes: 1),
        minimumFetchInterval: const Duration(hours: 1),
      ));
      debugPrint('Config settings set successfully');

      await _remoteConfig.setDefaults({
        _showPopupKey: false,
      });
      debugPrint('Defaults set: show_popup = false');

      final fetchStatus = await _remoteConfig.fetchAndActivate();
      debugPrint('Fetch and activate completed. Success: $fetchStatus');

      final showPopup = _remoteConfig.getBool(_showPopupKey);
      debugPrint('Remote config value for show_popup: $showPopup');

      if (showPopup) {
        debugPrint('Showing Christmas popup based on remote config');
        _showChristmasWishesIfNeeded();
      } else {
        debugPrint('Popup disabled by remote config');
      }
    } catch (e, stackTrace) {
      debugPrint('Failed to initialize remote config: $e');
      debugPrint('Stack trace: $stackTrace');
    }
  }

  Future<void> _checkAndUpdateConfig() async {
    try {
      debugPrint('Checking for config updates...');
      final updated = await _remoteConfig.fetchAndActivate();
      final showPopup = _remoteConfig.getBool(_showPopupKey);
      debugPrint('Config update status: $updated, show_popup: $showPopup');

      if (updated && showPopup) {
        debugPrint('Config updated and popup enabled, checking if should show');
        _showChristmasWishesIfNeeded();
      }
    } catch (e) {
      debugPrint('Failed to fetch remote config: $e');
    }
  }

  Future<void> _showChristmasWishesIfNeeded() async {
    final prefs = await SharedPreferences.getInstance();
    final hasSeenWishes = prefs.getBool(_hasSeenWishesKey) ?? false;
    debugPrint('Has user seen wishes before? $hasSeenWishes');

    if (!hasSeenWishes) {
      debugPrint('User has not seen wishes, showing popup');
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _showChristmasWishes();
      });
      await prefs.setBool(_hasSeenWishesKey, true);
      debugPrint('Marked wishes as seen in preferences');
    } else {
      debugPrint('User has already seen wishes, skipping popup');
    }
  }

  void _showChristmasWishes() {
    showDialog(
      context: context,
      builder: (BuildContext context) {
        return Dialog(
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(20),
          ),
          child: Container(
            padding: const EdgeInsets.all(24),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(20),
              gradient: const LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [Color(0xFF1E3C72), Color(0xFF2A5298)],
              ),
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(
                  Icons.favorite,
                  color: Colors.red,
                  size: 48,
                ),
                const SizedBox(height: 16),
                Text(
                  'Buon Natale Pupina!',
                  style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                      ),
                ),
                const SizedBox(height: 16),
                Text(
                  'Un piccolo aiuto per decidere quando andare a nuotare\n❤️ Spero ti sarà utile! ❤️',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                        color: Colors.white,
                      ),
                ),
                const SizedBox(height: 24),
                TextButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text(
                    'Ti amo tanto',
                    style: TextStyle(color: Colors.white),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  void _connectWebSocket() {
    _channel?.sink.close();
    _channel = WebSocketChannel.connect(
      Uri.parse('wss://badi-public.crowdmonitor.ch:9591/api'),
    );

    _channel!.stream.listen(
      (message) {
        _handleMessage(message);
        setState(() => _isConnected = true);
        _reconnectAttempts = 0;
      },
      onError: (error) {
        _handleConnectionError();
      },
      onDone: () {
        _handleConnectionError();
      },
    );

    _channel!.sink.add('all');
    // Set up periodic ping
    Future.delayed(const Duration(seconds: 30), _sendPing);
  }

  void _sendPing() {
    if (_channel != null) {
      _channel!.sink.add('all');
      Future.delayed(const Duration(seconds: 30), _sendPing);
    }
  }

  void _handleConnectionError() {
    setState(() => _isConnected = false);
    if (_reconnectAttempts < maxReconnectAttempts) {
      _reconnectAttempts++;
      Future.delayed(const Duration(seconds: 5), _connectWebSocket);
    }
  }

  void _handleMessage(String message) {
    try {
      final List<dynamic> data = jsonDecode(message);
      final cityPool = data.firstWhere(
        (pool) => pool['name'] == 'Hallenbad City',
        orElse: () => null,
      );

      if (cityPool != null) {
        setState(() {
          _isPoolOpen = true;
          _freePlaces = int.parse(cityPool['freespace'].toString());
          _totalCapacity = int.parse(cityPool['maxspace'].toString());
          _currentUsage = int.parse(cityPool['currentfill'].toString());
          // Calculate availability percentage
          _availabilityPercentage = _totalCapacity > 0
              ? (_freePlaces / _totalCapacity).clamp(0.0, 1.0) * 100
              : 0.0;
          _updateStatus();
        });
      }
    } catch (e) {
      debugPrint('Error processing message: $e');
    }
  }

  void _updateStatus() {
    // Updated thresholds based on percentage rather than absolute numbers
    if (_availabilityPercentage >= 40) {
      _status = 'High availability';
    } else if (_availabilityPercentage >= 20) {
      _status = 'Moderate availability';
    } else {
      _status = 'Low availability';
    }
  }

  void _updateTimeRemaining() {
    final now = DateTime.now();
    final closing = DateTime(
      now.year,
      now.month,
      now.day,
      22, // 22:00 closing time
      0,
    );

    if (now.hour >= 22 || now.hour < 6) {
      final opening = DateTime(
        now.year,
        now.month,
        now.day + (now.hour >= 22 ? 1 : 0),
        6, // 06:00 opening time
        0,
      );
      final difference = opening.difference(now);
      setState(() {
        _timeRemaining = 'Opens in ${_formatDuration(difference)}';
      });
    } else {
      final difference = closing.difference(now);
      setState(() {
        _timeRemaining = 'Closes in ${_formatDuration(difference)}';
      });
    }
  }

  String _formatDuration(Duration duration) {
    final hours = duration.inHours;
    final minutes = duration.inMinutes.remainder(60);
    final seconds = duration.inSeconds.remainder(60);
    return '$hours hours, $minutes minutes, $seconds seconds';
  }

  Color _getCapacityColor() {
    if (!_isPoolOpen) return Colors.grey.withOpacity(0.2);
    if (_availabilityPercentage >= 40) return Colors.green.withOpacity(0.2);
    if (_availabilityPercentage >= 20) return Colors.yellow.withOpacity(0.2);
    return Colors.red.withOpacity(0.7);
  }

  Widget _buildProgressBar() {
    final screenWidth = MediaQuery.of(context).size.width;
    final isLargeScreen = screenWidth > 600;
    final barWidth = isLargeScreen ? 600.0 : screenWidth;

    // Get colors for gradient based on availability percentage
    List<Color> getProgressColors() {
      if (_availabilityPercentage >= 75) {
        return [Colors.green.shade300, Colors.green];
      } else if (_availabilityPercentage >= 50) {
        return [Colors.yellow.shade300, Colors.yellow];
      } else {
        return [Colors.red.shade300, Colors.red];
      }
    }

    return Container(
      width: barWidth,
      height: 20,
      margin: const EdgeInsets.symmetric(vertical: 20),
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(10),
        child: Container(
          decoration: BoxDecoration(
            color: Colors.white.withOpacity(0.1),
            borderRadius: BorderRadius.circular(10),
          ),
          child: FractionallySizedBox(
            alignment: Alignment.centerLeft,
            widthFactor: (_availabilityPercentage / 100).clamp(0.0, 1.0),
            child: Container(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.centerLeft,
                  end: Alignment.centerRight,
                  colors: getProgressColors(),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildStatsGrid(BuildContext context) {
    final screenWidth = MediaQuery.of(context).size.width;
    final isLargeScreen = screenWidth > 600;
    final gridWidth = isLargeScreen ? 600.0 : screenWidth;
    final itemHeight = 100.0; // Fixed height for each grid item
    final itemWidth = (gridWidth - 48) / 3; // Account for padding and spacing

    return Container(
      width: gridWidth,
      padding: const EdgeInsets.all(16),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          SizedBox(
            width: itemWidth,
            height: itemHeight,
            child: StatBox(
              title: 'Total Capacity',
              value: _totalCapacity.toString(),
            ),
          ),
          SizedBox(
            width: itemWidth,
            height: itemHeight,
            child: StatBox(
              title: 'Current Usage',
              value: _currentUsage.toString(),
            ),
          ),
          SizedBox(
            width: itemWidth,
            height: itemHeight,
            child: StatBox(
              title: 'Availability',
              value: '${_availabilityPercentage.round()}%',
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildCapacityCircle() {
    return Container(
      width: 300,
      height: 300,
      margin: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: _getCapacityColor(),
        shape: BoxShape.circle,
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.2),
            blurRadius: 30,
          ),
        ],
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            _freePlaces.toString(),
            style: Theme.of(context).textTheme.displayLarge?.copyWith(
                  fontSize: 96, // Increased font size
                  fontWeight: FontWeight.bold,
                ),
          ),
          Text(
            'Free Places',
            style: Theme.of(context).textTheme.titleLarge?.copyWith(
                  fontSize: 24, // Increased font size
                ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final screenWidth = MediaQuery.of(context).size.width;
    final isLargeScreen = screenWidth > 600;
    final contentWidth = isLargeScreen ? 600.0 : screenWidth;

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
          title: const Text('Hallenbad City - Live Capacity'),
          actions: [
            Padding(
              padding: const EdgeInsets.only(right: 16.0),
              child: IconButton(
                icon: const Icon(Icons.insights),
                onPressed: () {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                        builder: (context) => const PredictionsPage()),
                  );
                },
                tooltip: 'View Predictions',
              ),
            ),
          ],
        ),
        body: Container(
          width: screenWidth,
          child: SafeArea(
            child: SingleChildScrollView(
              child: Center(
                child: SizedBox(
                  width: contentWidth,
                  child: Column(
                    children: [
                      if (!_isConnected)
                        Container(
                          margin: const EdgeInsets.all(16),
                          padding: const EdgeInsets.all(16),
                          decoration: BoxDecoration(
                            color: Colors.red.withOpacity(0.1),
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              const Flexible(
                                child: Text('Connection lost. Reconnecting...'),
                              ),
                              if (_reconnectAttempts >= maxReconnectAttempts)
                                TextButton(
                                  onPressed: () {
                                    _reconnectAttempts = 0;
                                    _connectWebSocket();
                                  },
                                  child: const Text('Try Again'),
                                ),
                            ],
                          ),
                        ),
                      _buildCapacityCircle(),
                      _buildProgressBar(),
                      _buildStatsGrid(context),
                      Padding(
                        padding: const EdgeInsets.all(16),
                        child: Text(
                          _status,
                          style: Theme.of(context).textTheme.headlineMedium,
                          textAlign: TextAlign.center,
                        ),
                      ),
                      // Add time remaining at the bottom
                      Padding(
                        padding: const EdgeInsets.only(bottom: 24),
                        child: Text(
                          _timeRemaining,
                          style:
                              Theme.of(context).textTheme.bodyMedium?.copyWith(
                                    fontSize: 14,
                                    color: Colors.white70,
                                  ),
                          textAlign: TextAlign.center,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  @override
  void dispose() {
    _configCheckTimer?.cancel();
    _channel?.sink.close();
    super.dispose();
  }
}

class StatBox extends StatelessWidget {
  final String title;
  final String value;

  const StatBox({super.key, required this.title, required this.value});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.1),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            title,
            style: Theme.of(context).textTheme.titleMedium?.copyWith(
                  color: Colors.white70,
                ),
            textAlign: TextAlign.center,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 8),
          Text(
            value,
            style: Theme.of(context).textTheme.titleLarge,
            textAlign: TextAlign.center,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}
