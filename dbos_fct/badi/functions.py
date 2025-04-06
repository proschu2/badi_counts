from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Any, List, Dict, Optional
from enum import Enum
from prophet import Prophet
from prophet.serialize import model_to_json, model_from_json
import pandas as pd
from sqlalchemy import text
import numpy as np
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from dbos import DBOS

# Define time periods globally
TIME_PERIODS = {
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

app = FastAPI()
dbos_app = DBOS(fastapi=app)


class TimePeriod(str, Enum):
    """Time periods for predictions (regular periods only)"""

    EARLY_MORNING = "early_morning"
    LATE_MORNING = "late_morning"
    LUNCH = "lunch"
    AFTERNOON = "afternoon"
    AFTER_WORK = "after_work"
    EVENING = "evening"


class PredictionInput(BaseModel):
    """
    Input model for prediction endpoints.

    Contains historical data points and configuration for predictions.
    """

    timestamps: List[datetime] = Field(
        ...,
        description="List of timestamps for historical observations (UTC or with timezone)",
    )
    values: List[float] = Field(
        ...,
        description="List of freespace percentage values (0-100) corresponding to timestamps",
    )
    days: int = Field(
        5, description="Number of days to forecast into the future", ge=1, le=14
    )
    is_full_history: bool = Field(
        False,
        description="Set to True when providing complete historical data for model training",
    )


class TimePeriodPrediction(BaseModel):
    """Aggregated prediction for a specific time period of the day"""

    predicted_freespace_percentage: float = Field(
        ...,
        ge=0,
        le=100,
        description="Average predicted free space percentage for the time period",
    )
    period: TimePeriod = Field(
        ..., description="Time period identifier (e.g. early_morning, lunch)"
    )


class DetailedPrediction(BaseModel):
    """Detailed prediction for a specific point in time"""

    timestamp: datetime = Field(
        ..., description="Timestamp for this prediction (Europe/Zurich timezone)"
    )
    predicted_freespace_percentage: float = Field(
        ..., ge=0, le=100, description="Predicted free space percentage"
    )
    lower_bound: float = Field(
        ...,
        ge=0,
        le=100,
        description="Lower bound of the prediction interval (95% confidence)",
    )
    upper_bound: float = Field(
        ...,
        ge=0,
        le=100,
        description="Upper bound of the prediction interval (95% confidence)",
    )
    time_period: Optional[TimePeriod] = Field(
        None,
        description="The time period this prediction belongs to (e.g., morning, afternoon)",
    )


# Regular time periods (excluding special periods)
REGULAR_PERIODS = {
    k: v
    for k, v in TIME_PERIODS.items()
    if k not in ["weekend_day", "closed", "peak_hours"]
}


class PeriodPredictions(BaseModel):
    """
    Model for regular daily period predictions
    (excluding special periods like weekend_day, closed, peak_hours)
    """

    early_morning: Optional[TimePeriodPrediction] = Field(
        None, description="06:00-09:00"
    )
    late_morning: Optional[TimePeriodPrediction] = Field(
        None, description="09:00-11:00"
    )
    lunch: Optional[TimePeriodPrediction] = Field(None, description="11:00-13:00")
    afternoon: Optional[TimePeriodPrediction] = Field(None, description="13:00-16:00")
    after_work: Optional[TimePeriodPrediction] = Field(None, description="16:00-19:00")
    evening: Optional[TimePeriodPrediction] = Field(None, description="19:00-22:00")


class DayPrediction(BaseModel):
    """Complete predictions for a single day"""

    last_updated: datetime = Field(
        ...,
        description="Timestamp when these predictions were generated (Europe/Zurich timezone)",
    )
    predictions: List[DetailedPrediction] = Field(
        ...,
        description="List of detailed predictions at 30-minute intervals during operating hours",
    )
    periods: PeriodPredictions = Field(
        ..., description="Aggregated predictions for each time period of the day"
    )


class PredictionResponse(BaseModel):
    """Response model for prediction endpoints"""

    message: str = Field(
        ..., description="Status message indicating success or any relevant information"
    )
    predictions: Dict[str, DayPrediction] = Field(
        ...,
        description="Predictions organized by date (YYYY-MM-DD format), each containing detailed "
        "and period-based predictions",
    )


@dbos_app.transaction()
def save_prophet_model(model: Prophet) -> None:
    """Save Prophet model to database"""
    model_json = model_to_json(model)
    sql = text(""" INSERT INTO prophet_models 
        (model_type, model_data, last_training_date, metadata) 
        VALUES (:model_type, :model_data, :last_training_date, :metadata) """)
    dbos_app.sql_session.execute(
        sql,
        {
            "model_type": "badi_predictions",
            "model_data": model_json,
            "last_training_date": datetime.now().date(),
            "metadata": json.dumps(
                {"timestamp": datetime.now().isoformat(), "type": "prophet_model"}
            ),
        },
    )


@dbos_app.transaction()
def load_latest_model() -> Prophet:
    """Load the latest Prophet model from database"""
    sql = text(
        """
        SELECT model_data 
        FROM prophet_models 
        WHERE model_type = :model_type
        ORDER BY created_at DESC 
        LIMIT 1
        """
    )
    result = dbos_app.sql_session.execute(
        sql,
        {"model_type": "badi_predictions"},
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="No stored model found")
    return model_from_json(result[0])


@dbos_app.step()
def process_and_predict(
    data: PredictionInput, model: Optional[Any] = None
) -> Dict[str, DayPrediction]:
    """
    DBOS step to process data and run Prophet forecasting.

    Args:
        data: PredictionInput object containing timestamps, values, and prediction window size

    Returns:
        Dict[str, DayPrediction]: Dictionary mapping dates to daily predictions,
        containing both detailed predictions and period summaries
    """
    df, latest_timestamp = prepare_data(data)

    if not data.is_full_history:
        try:
            assert model is not None, (
                "Model should be provided when not using full history"
            )
            try:
                history = model.history
            except AttributeError:
                raise HTTPException(status_code=500, detail="Model history not found")
            history = history.filter(df.columns)
            updated_df = pd.concat([history, df], ignore_index=True).drop_duplicates()
            model.fit(updated_df)
        except (AssertionError, HTTPException):
            model = fit_model(df)
    else:
        # If full history is provided, create a new model
        model = fit_model(df)
    future = prepare_future_dates(latest_timestamp, data.days, TIME_PERIODS)
    # Generate and format predictions
    formatted_predictions = format_predictions(
        model, future, latest_timestamp, data.days
    )
    return formatted_predictions, model


def format_predictions(
    model: Prophet, future: pd.DataFrame, latest_timestamp: pd.Timestamp, days: int
) -> Dict[str, DayPrediction]:
    """Format Prophet predictions into the expected output structure"""
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

        # Determine time period using REGULAR_PERIODS
        time_period = None
        for period_name, (start, end) in REGULAR_PERIODS.items():
            if start <= hour < end:
                time_period = period_name
                break

        if day not in forecasts_by_day:
            forecasts_by_day[day] = {
                "predictions": [],
                "periods": {
                    p: {"value": 0, "count": 0} for p in REGULAR_PERIODS.keys()
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


def prepare_data(data: PredictionInput) -> tuple[pd.DataFrame, pd.Timestamp, int]:
    """Prepare data frame for Prophet model"""
    if len(data.timestamps) != len(data.values):
        raise ValueError("Timestamps and values must have same length")

    # Filter out negative values and keep corresponding timestamps
    valid_indices = [i for i, v in enumerate(data.values) if v >= 0]
    filtered_timestamps = [data.timestamps[i] for i in valid_indices]
    filtered_values = [data.values[i] for i in valid_indices]

    if not filtered_values:
        raise ValueError("No valid values remaining after filtering out negatives")

    # Create DataFrame for Prophet with filtered timestamps
    base_df = pd.DataFrame(
        {
            "ds": [
                ts.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
                for ts in filtered_timestamps
            ],
            "y": filtered_values,
        }
    )

    # Get the actual latest timestamp before any processing
    latest_timestamp = pd.Timestamp(max(data.timestamps))

    # Add closed hours with zero values
    start_date = base_df["ds"].min().normalize()
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

    df["hour_sin"] = np.sin(2 * np.pi * df["ds"].dt.hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["ds"].dt.hour / 24)
    df["weekday"] = df["ds"].dt.weekday

    # Add time period features
    hour = df["ds"].dt.hour
    for period_name, (start, end) in TIME_PERIODS.items():
        if period_name == "weekend_day":
            df[period_name] = (
                (hour >= start) & (hour < end) & (df["ds"].dt.weekday >= 5)
            ).astype(int)
        elif period_name == "closed":
            df[period_name] = ((hour >= start) | (hour < end)).astype(int)
        else:
            df[period_name] = ((hour >= start) & (hour < end)).astype(int)

    return df, latest_timestamp


def get_empty_model() -> Prophet:
    model = Prophet(
        daily_seasonality=True,
        weekly_seasonality=False,
        yearly_seasonality=False,
        changepoint_prior_scale=0.005,  # Reduced from 0.05 for smoother predictions
        seasonality_mode="additive",
        seasonality_prior_scale=5.0,
        holidays_prior_scale=0.1,
    )

    model.add_country_holidays(country_name="CH")
    for period_name in TIME_PERIODS.keys():
        model.add_regressor(period_name)
    return model


def fit_model(df: pd.DataFrame) -> Prophet:
    """Fit a new Prophet model to the provided DataFrame"""
    model = get_empty_model()
    model.fit(df)
    return model


def prepare_future_dates(
    latest_timestamp: pd.Timestamp, days: int, periods: dict
) -> pd.DataFrame:
    """Prepare future dates dataframe with all necessary features"""
    current_date = latest_timestamp.date()
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
        current_date + pd.Timedelta(days=days), datetime.min.time()
    ).replace(hour=23, minute=59)

    future = pd.date_range(
        start=pd.to_datetime(prediction_start).tz_localize(None),
        end=end_date,
        freq="30min",
        tz="Europe/Zurich",
    ).tz_localize(None)

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

    return future


@dbos_app.workflow()
@app.post("/predict")
async def forecast_prophet(data: PredictionInput) -> PredictionResponse:
    """
    Endpoint for generating predictions using the latest trained model.

    Returns prediction results for each requested day, including:
    - Detailed predictions every 30 minutes
    - Aggregated predictions by time period (morning, afternoon, etc.)
    - Confidence intervals for each prediction
    """
    model = load_latest_model()
    result, _ = process_and_predict(data, model)
    return PredictionResponse(
        message="Predictions generated successfully",
        predictions=result,
    )


@dbos_app.workflow()
@app.post("/fit_full_model")
async def fit_full_model_endpoint(data: PredictionInput) -> PredictionResponse:
    """
    Endpoint to train a new model using complete historical data.

    Should be called with a full history of observations to establish baseline patterns.
    After training, the model will be saved and used for subsequent predictions.

    Like the predict endpoint, returns predictions for the requested forecast window.
    """
    data.is_full_history = True
    result, model = process_and_predict(data)
    save_prophet_model(model)
    return PredictionResponse(
        message="Full model fitted and stored successfully",
        predictions=result,
    )


# if __name__ == "__main__":
#     import uvicorn

#     uvicorn.run(app, host="0.0.0.0", port=1234)
