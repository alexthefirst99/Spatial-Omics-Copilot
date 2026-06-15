const path = require('path');
const webpack = require('webpack');

module.exports = {
    entry: './src/index.js',
    output: {
        path: path.resolve(__dirname, 'dash_viv_viewer'),
        filename: 'dash_viv_viewer.min.js',
        library: 'dash_viv_viewer',
        libraryTarget: 'window',
    },
    plugins: [
        // Force everything into a single JS file — Dash only serves registered assets
        new webpack.optimize.LimitChunkCountPlugin({ maxChunks: 1 }),
    ],
    externals: {
        react: 'React',
        'react-dom': 'ReactDOM',
        'plotly.js': 'Plotly',
    },
    // Disable code splitting — Dash only serves the single registered JS file
    optimization: {
        splitChunks: false,
        runtimeChunk: false,
    },
    module: {
        rules: [
            {
                test: /\.(react\.js|jsx?)$/,
                exclude: /node_modules/,
                use: {
                    loader: 'babel-loader',
                    options: {
                        presets: ['@babel/preset-env', '@babel/preset-react'],
                    },
                },
            },
            // Transpile ESM packages (viv, deck.gl, luma.gl, geotiff)
            {
                test: /\.jsx?$/,
                include: /node_modules\/@hms-dbmi|node_modules\/@deck\.gl|node_modules\/@luma\.gl|node_modules\/deck\.gl|node_modules\/geotiff/,
                use: {
                    loader: 'babel-loader',
                    options: {
                        presets: ['@babel/preset-env', '@babel/preset-react'],
                    },
                },
            },
        ],
    },
    resolve: {
        extensions: ['.react.js', '.js', '.jsx'],
        alias: {
            react: path.resolve('./node_modules/react'),
            'react-dom': path.resolve('./node_modules/react-dom'),
        },
    },
    performance: { hints: false },
};
