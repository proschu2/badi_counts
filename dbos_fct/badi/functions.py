from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Any, List, Dict, Optional
from enum import Enum
from prophet import Prophet
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from dbos import DBOS

app = FastAPI()
dbos_app = DBOS(fastapi=app)


class TimePeriod(str, Enum):
    EARLY_MORNING = "early_morning"
    LATE_MORNING = "late_morning"
    LUNCH = "lunch"
    AFTERNOON = "afternoon"
    AFTER_WORK = "after_work"
    EVENING = "evening"
    PEAK_HOURS = "peak_hours"
    WEEKEND_DAY = "weekend_day"


class PredictionInput(BaseModel):
    timestamps: List[datetime]
    values: List[float]
    days: int = 5


class TimePeriodPrediction(BaseModel):
    predicted_freespace_percentage: float = Field(..., ge=0, le=100)
    period: TimePeriod


class DetailedPrediction(BaseModel):
    timestamp: datetime
    predicted_freespace_percentage: float = Field(..., ge=0, le=100)
    lower_bound: float = Field(..., ge=0, le=100)
    upper_bound: float = Field(..., ge=0, le=100)
    time_period: Optional[TimePeriod] = None


class PeriodPredictions(BaseModel):
    early_morning: Optional[TimePeriodPrediction] = None
    late_morning: Optional[TimePeriodPrediction] = None
    lunch: Optional[TimePeriodPrediction] = None
    afternoon: Optional[TimePeriodPrediction] = None
    after_work: Optional[TimePeriodPrediction] = None
    evening: Optional[TimePeriodPrediction] = None


class DayPrediction(BaseModel):
    last_updated: datetime
    predictions: List[DetailedPrediction]
    periods: PeriodPredictions


class PredictionResponse(BaseModel):
    message: str
    predictions: Dict[str, DayPrediction]


@dbos_app.step()
def process_and_predict(data: PredictionInput) -> Dict[str, Any]:
    """
    DBOS step to process data and run Prophet forecasting
    """
    if len(data.timestamps) != len(data.values):
        raise ValueError("Timestamps and values must have same length")

    # Create DataFrame for Prophet with all timestamps
    base_df = pd.DataFrame(
        {
            "ds": [
                ts.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
                for ts in data.timestamps
            ],
            "y": data.values,
        }
    )

    # Get the actual latest timestamp before any processing
    latest_timestamp = pd.Timestamp(max(data.timestamps))

    # Add closed hours with zero values
    start_date = base_df["ds"].min().normalize()
    # Don't add an extra day, just go up to the last actual timestamp's date
    end_date = (
        latest_timestamp.normalize().replace(hour=23, minute=59).tz_localize(None)
    )
    all_times = pd.date_range(start=start_date, end=end_date, freq="30min")

    # Create complete DataFrame with all times
    df = pd.DataFrame({"ds": all_times})
    df["y"] = np.nan
    df.loc[(df["ds"].dt.hour >= 22) | (df["ds"].dt.hour < 6), "y"] = 0
    df = df.dropna()

    # Drop future rows if latest timestamp is before 21:30
    if latest_timestamp.hour < 21 or (
        latest_timestamp.hour == 21 and latest_timestamp.minute < 30
    ):
        df = df[df["ds"] <= pd.to_datetime(latest_timestamp).tz_localize(None)]

    # Merge actual data with complete DataFrame
    df = pd.concat([base_df, df], ignore_index=True).sort_values("ds")

    # Calculate training data size
    days_of_data = (df["ds"].max() - df["ds"].min()).days

    # Initialize Prophet model with configuration
    model = Prophet(
        daily_seasonality=True,
        weekly_seasonality=False,
        yearly_seasonality=False,
        changepoint_prior_scale=0.005,  # Reduced from 0.05 for smoother predictions
        seasonality_mode="additive",
        seasonality_prior_scale=5.0,
        holidays_prior_scale=0.1,
    )

    # Add holidays if enough data
    if days_of_data > 3:
        model.add_country_holidays(country_name="CH")

    # Add time features
    df["hour_sin"] = np.sin(2 * np.pi * df["ds"].dt.hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["ds"].dt.hour / 24)
    df["weekday"] = df["ds"].dt.weekday

    # Define time periods
    periods = {
        "early_morning": (6, 9),
        "late_morning": (9, 11),
        "lunch": (11, 13),
        "afternoon": (13, 16),
        "after_work": (16, 19),
        "peak_hours": (17, 19),
        "weekend_day": (10, 18),
        "evening": (19, 22),
        "closed": (22, 6),
    }

    # Add time period features
    hour = df["ds"].dt.hour
    for period_name, (start, end) in periods.items():
        if period_name == "weekend_day":
            df[period_name] = (
                (hour >= start) & (hour < end) & (df["ds"].dt.weekday >= 5)
            ).astype(int)
        elif period_name == "closed":
            df[period_name] = ((hour >= start) | (hour < end)).astype(int)
        else:
            df[period_name] = ((hour >= start) & (hour < end)).astype(int)
        model.add_regressor(period_name)

    # Fit model
    model.fit(df)

    # Get the latest timestamp and its date
    current_date = latest_timestamp.date()

    # Calculate periods for future predictions including remaining time today
    current_minute = latest_timestamp.minute
    current_hour = latest_timestamp.hour

    # Round latest timestamp to nearest 30-min mark for prediction start
    if current_minute < 30:
        prediction_start = latest_timestamp.replace(minute=30, second=0, microsecond=0)
    else:
        prediction_start = (latest_timestamp + pd.Timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        )

    # Create future dates from prediction_start until end of last prediction day
    end_date = datetime.combine(
        current_date + pd.Timedelta(days=data.days), datetime.min.time()
    ).replace(hour=23, minute=59)

    future = pd.date_range(
        start=pd.to_datetime(prediction_start).tz_localize(None),
        end=end_date,
        freq="30min",
        tz="Europe/Zurich",
    ).tz_localize(None)  # Remove timezone for Prophet

    future = pd.DataFrame({"ds": future})

    # Filter operating hours, but keep current day predictions until 22:00
    future = future[
        ((future["ds"].dt.hour >= 6) & (future["ds"].dt.hour < 22))
        | (
            (future["ds"].dt.date == current_date)
            & (future["ds"].dt.hour >= latest_timestamp.hour)
            & (future["ds"].dt.hour < 22)
        )
    ]
    # Add features to future dataframe
    hour = future["ds"].dt.hour
    future["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    future["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    future["weekday"] = future["ds"].dt.weekday

    # Add time periods to future
    for period_name, (start, end) in periods.items():
        if period_name == "weekend_day":
            future[period_name] = (
                (hour >= start) & (hour < end) & (future["ds"].dt.weekday >= 5)
            ).astype(int)
        elif period_name == "closed":
            future[period_name] = ((hour >= start) | (hour < end)).astype(int)
        else:
            future[period_name] = ((hour >= start) & (hour < end)).astype(int)

    # Generate and clip predictions
    forecast = model.predict(future)
    forecast["yhat"] = forecast["yhat"].clip(lower=0, upper=100)
    forecast["yhat_lower"] = forecast["yhat_lower"].clip(lower=0, upper=100)
    forecast["yhat_upper"] = forecast["yhat_upper"].clip(lower=0, upper=100)

    # Group predictions by day and calculate period averages
    forecasts_by_day = {}
    for _, row in forecast.iterrows():
        timestamp = pd.Timestamp(row["ds"]).tz_localize("Europe/Zurich")
        day = timestamp.strftime("%Y-%m-%d")
        hour = timestamp.hour

        # Determine time period
        time_period = None
        if 6 <= hour < 9:
            time_period = "early_morning"
        elif 9 <= hour < 11:
            time_period = "late_morning"
        elif 11 <= hour < 13:
            time_period = "lunch"
        elif 13 <= hour < 16:
            time_period = "afternoon"
        elif 16 <= hour < 19:
            time_period = "after_work"
        elif 19 <= hour < 22:
            time_period = "evening"

        if day not in forecasts_by_day:
            forecasts_by_day[day] = {
                "predictions": [],
                "periods": {
                    p: {"value": 0, "count": 0}
                    for p in [
                        "early_morning",
                        "late_morning",
                        "lunch",
                        "afternoon",
                        "after_work",
                        "evening",
                    ]
                },
            }

        # Add prediction with rounded percentages
        forecasts_by_day[day]["predictions"].append(
            {
                "timestamp": timestamp.to_pydatetime(),
                "predicted_freespace_percentage": np.round(float(row["yhat"]), 2),
                "lower_bound": np.round(float(row["yhat_lower"]), 2),
                "upper_bound": np.round(float(row["yhat_upper"]), 2),
                "time_period": time_period,
            }
        )

        # Update period averages
        if time_period:
            forecasts_by_day[day]["periods"][time_period]["value"] += float(row["yhat"])
            forecasts_by_day[day]["periods"][time_period]["count"] += 1

    # Calculate final period averages and format output
    formatted_predictions = {}
    for day, data in forecasts_by_day.items():
        period_predictions = PeriodPredictions(
            **{
                period: TimePeriodPrediction(
                    predicted_freespace_percentage=np.round(
                        values["value"] / values["count"], 1
                    )
                    if values["count"] > 0
                    else 0,
                    period=TimePeriod(period),
                )
                for period, values in data["periods"].items()
                if values["count"] > 0
            }
        )

        formatted_predictions[day] = DayPrediction(
            last_updated=datetime.now(ZoneInfo("Europe/Zurich")),
            predictions=[DetailedPrediction(**pred) for pred in data["predictions"]],
            periods=period_predictions,
        ).model_dump(exclude_none=True, exclude_unset=True)

    return formatted_predictions


@dbos_app.workflow()
@app.post("/predict")
async def forecast_prophet(data: PredictionInput) -> Dict[str, Any]:
    """
    Combined DBOS workflow and FastAPI endpoint for Prophet forecasting
    """
    return process_and_predict(data)


# if __name__ == "__main__":
#     import uvicorn

#     uvicorn.run(app, host="0.0.0.0", port=1234)
