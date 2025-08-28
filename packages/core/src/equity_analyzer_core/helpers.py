def check_data_size(df):
    return df.size


def check_data_shape(df):
    return df.shape


def check_null_values(df):
    return df.isnull().sum()


def check_top_5_price_counts(df, price_column):
    return df[price_column].value_counts().head(5)


def get_column_names(df):
    return df.columns


def get_head(df, price_column, features, number_of_rows=5):
    return df[[price_column] + features].head(number_of_rows)
