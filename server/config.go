package main

import (
	"fmt"
	"log"
	"os"
	"strconv"
	"time"
)

type Config struct {
	Port                       int
	APIKey                     string
	DashboardKey               string
	MaxLogLines                int
	MoveTimeout                time.Duration
	AllowUnauthenticatedAccess bool
}

func loadConfig() *Config {
	cfg := &Config{
		Port:        8080,
		MaxLogLines: 1000,
	}

	cfg.MoveTimeout = 30 * time.Second
	if mtStr := os.Getenv("SERVER_MOVE_TIMEOUT"); mtStr != "" {
		if d, err := time.ParseDuration(mtStr); err == nil {
			cfg.MoveTimeout = d
		}
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
	cfg.DashboardKey = os.Getenv("SERVER_DASHBOARD_KEY")
	cfg.AllowUnauthenticatedAccess = os.Getenv("MAD_ALLOW_NO_AUTH") == "1"
	if cfg.APIKey == "" && cfg.DashboardKey == "" && !cfg.AllowUnauthenticatedAccess {
		log.Fatal("FATAL: Neither SERVER_API_KEY nor SERVER_DASHBOARD_KEY is set. Refusing to start without authentication. Set at least one key or set MAD_ALLOW_NO_AUTH=1 to explicitly allow unauthenticated access.")
	}
	if cfg.APIKey == "" {
		log.Println("WARNING: SERVER_API_KEY is not set, API auth is disabled")
	}
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
