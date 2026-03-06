package main

import (
	"fmt"
	"log"
	"net/http"
)

func main() {
	cfg := loadConfig()
	hub := NewHub(cfg)
	go hub.Run()

	mux := http.NewServeMux()
	registerRoutes(mux, hub, cfg)

	log.Printf("MAD server listening on :%d", cfg.Port)
	log.Fatal(http.ListenAndServe(fmt.Sprintf(":%d", cfg.Port), mux))
}
