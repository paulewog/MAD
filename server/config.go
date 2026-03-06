package main

import (
	"fmt"
	"log"
	"os"
	"strconv"
)

type Config struct {
	Port         int
	APIKey       string
	DashboardKey string
	MaxLogLines  int
}

func loadConfig() *Config {
	cfg := &Config{
		Port:        8080,
		MaxLogLines: 1000,
	}

	if portStr := os.Getenv("SERVER_PORT"); portStr != "" {
		p, err := strconv.Atoi(portStr)
		if err != nil {
			fmt.Fprintf(os.Stderr, "invalid SERVER_PORT %q: %v\n", portStr, err)
			os.Exit(1)
		}
		cfg.Port = p
	}

	cfg.APIKey = os.Getenv("SERVER_API_KEY")
	if cfg.APIKey == "" {
		log.Println("WARNING: SERVER_API_KEY is not set, API auth is disabled")
	}

	cfg.DashboardKey = os.Getenv("SERVER_DASHBOARD_KEY")
	if cfg.DashboardKey == "" {
		log.Println("WARNING: SERVER_DASHBOARD_KEY is not set, dashboard is unprotected")
	}

	if maxStr := os.Getenv("SERVER_MAX_LOG_LINES"); maxStr != "" {
		m, err := strconv.Atoi(maxStr)
		if err == nil && m > 0 {
			cfg.MaxLogLines = m
		}
	}

	return cfg
}
