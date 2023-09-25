import matplotlib.pyplot as plt
import numpy as np

def plot_net_worth(monthly_net_worth, loan_repayment_dates, monthly_details=None):
    plt.style.use('ggplot')  # Use the ggplot style for a cleaner appearance

    plt.figure(figsize=(12, 6))
    plt.plot(monthly_net_worth, '-o', markersize=3, label="Net Worth", color='blue')

    # If monthly_details is provided, plot the values
    if monthly_details is not None:
        for key, values in monthly_details.items():
            plt.plot(values, '-o', markersize=3, label=key.capitalize())

    for loan_name, date in loan_repayment_dates.items():
        plt.axvline(x=date, color='r', linestyle='--')
        plt.annotate(loan_name, (date, min(monthly_net_worth)), textcoords="offset points", xytext=(0,10), ha='center')

    # Add vertical dotted lines every 12 units
    for i in range(0, len(monthly_net_worth), 12):
        plt.axvline(x=i, color='white', linewidth=1.8)

    plt.title('Net Worth and Financial Details Over Time')
    plt.xlabel('Months')
    plt.ylabel('Amount ($)')  # Added $ unit of measure to the y-axis
    plt.legend()
    plt.grid(True,linestyle='--', linewidth=0.8)
    plt.tight_layout()
    plt.show()

# Example:
# monthly_net_worth, monthly_details, loan_repayment_dates = compute_net_worth_over_time(10, bank_account, investments, loans, incomes, expenses)
# plot_net_worth(monthly_net_worth, loan_repayment_dates, monthly_details)

def plot_with_confidence_intervals(data, x_labels=None, 
                                   title="Line Chart with Confidence Intervals", 
                                   rolling_window=None):
    """
    Plot a line chart with confidence intervals for the mean.

    Parameters:
    - data: List of lists. Each inner list represents data for one line.
    - x_labels: Labels for x axis.
    - title: Title of the plot.
    - rolling_window: Size of the rolling window to apply on the data.
    """
    
    data = np.array(data)
    
    if rolling_window:
        data_rolled = np.apply_along_axis(lambda x: np.convolve(x, 
                                                                np.ones(rolling_window)/rolling_window, mode='valid'), 
                                          axis=1, arr=data)
    else:
        data_rolled = data

    means = np.mean(data_rolled, axis=0)
    std_dev = np.std(data_rolled, axis=0)
    n = len(data_rolled)
    
    # Calculate the 95% confidence intervals
    z_value = 1.96  # for 95% CI
    error_margin = z_value * (std_dev / np.sqrt(n))
    lower_bound = means - error_margin
    upper_bound = means + error_margin

    # Plotting
    plt.figure(figsize=(9, 5))
    plt.plot(means, color="blue", label="Mean")
    plt.fill_between(range(len(means)), lower_bound, upper_bound, color="blue", alpha=0.2)

    if x_labels:
        offset = (rolling_window - 1) // 2 if rolling_window else 0
        adjusted_labels = x_labels[offset:-offset] if rolling_window else x_labels
        plt.xticks(ticks=range(len(adjusted_labels)), labels=adjusted_labels)

    plt.title(title)
    plt.tight_layout()
    plt.legend()
    plt.show()
